"""The only place PC Control touches Windows, and the only place it may.

Everything here is `ctypes` against user32/shell32/ole32 plus `psutil`, which
the project already depends on. No new dependency, and -- far more importantly
-- NO SHELL. There is no `subprocess` import in this module and no string is
ever handed to cmd.exe or PowerShell to parse. A tool that needs Windows to do
something calls a typed function below; it cannot compose a command line,
because there is no command line to compose.

That is the whole security posture of PC Control V1 in one sentence: the model
picks a tool and typed arguments, and the arguments reach a Win32 call as
values, never as syntax.

The COM plumbing at the bottom exists for the same reason. Reading and setting
the master volume needs IAudioEndpointVolume, and the usual route is
pycaw+comtypes. Neither is installed, and the alternative that avoids them --
shelling out to PowerShell -- is exactly what this module refuses to do. So the
three interfaces are called directly through their vtables. It is more code
than `pip install pycaw`, and it is the version that cannot be turned into a
command-injection sink.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import POINTER, byref, c_float, c_int, c_void_p, wintypes
from typing import Callable

logger = logging.getLogger("nano.pc_control.winapi")

IS_WINDOWS = hasattr(ctypes, "windll")


class WindowsUnavailable(RuntimeError):
    """Raised when a Win32 entry point is used off Windows."""


def _require_windows() -> None:
    if not IS_WINDOWS:
        raise WindowsUnavailable("PC control requires Windows")


# --------------------------------------------------------------------------
#  user32 / window management
# --------------------------------------------------------------------------

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_MAXIMIZE = 3
SW_SHOWNOACTIVATE = 4
SW_MINIMIZE = 6
SW_RESTORE = 9

WM_CLOSE = 0x0010

GW_OWNER = 4

#: Extended styles that mark a window as chrome rather than something the user
#: thinks of as an open application: tool windows (floating palettes) and
#: windows that deliberately keep themselves out of the taskbar.
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
GWL_EXSTYLE = -20

DWMWA_CLOAKED = 14


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


def _user32():
    _require_windows()
    return ctypes.windll.user32


def enum_top_level_windows(callback: Callable[[int], None]) -> None:
    """Invoke ``callback`` once per top-level window handle."""
    user32 = _user32()
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _thunk(hwnd, _lparam):
        try:
            callback(int(hwnd))
        except Exception:
            logger.debug("window enumeration callback failed", exc_info=True)
        return True

    user32.EnumWindows(proc(_thunk), 0)


def window_title(hwnd: int) -> str:
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def window_class(hwnd: int) -> str:
    user32 = _user32()
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def window_pid(hwnd: int) -> int:
    user32 = _user32()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, byref(pid))
    return int(pid.value)


def is_window(hwnd: int) -> bool:
    return bool(_user32().IsWindow(hwnd))


def is_window_visible(hwnd: int) -> bool:
    return bool(_user32().IsWindowVisible(hwnd))


def is_iconic(hwnd: int) -> bool:
    return bool(_user32().IsIconic(hwnd))


def is_zoomed(hwnd: int) -> bool:
    return bool(_user32().IsZoomed(hwnd))


def window_owner(hwnd: int) -> int:
    return int(_user32().GetWindow(hwnd, GW_OWNER) or 0)


def window_ex_style(hwnd: int) -> int:
    user32 = _user32()
    getter = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    return int(getter(hwnd, GWL_EXSTYLE) or 0)


def is_cloaked(hwnd: int) -> bool:
    """True for a window Windows is deliberately hiding.

    UWP applications keep invisible host windows alive that pass every classic
    visibility test; DWM reports them as "cloaked". Without this check
    window.list is full of ghost entries the user cannot see and cannot act on.
    """
    if not IS_WINDOWS:
        return False
    try:
        cloaked = ctypes.c_int(0)
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(DWMWA_CLOAKED),
            byref(cloaked), ctypes.sizeof(cloaked),
        )
        return result == 0 and cloaked.value != 0
    except Exception:
        return False


def show_window(hwnd: int, command: int) -> bool:
    return bool(_user32().ShowWindow(hwnd, command))


def window_placement_state(hwnd: int) -> str:
    """"minimized" / "maximized" / "normal", read from the OS."""
    user32 = _user32()
    placement = _WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(_WINDOWPLACEMENT)
    if not user32.GetWindowPlacement(hwnd, byref(placement)):
        return "unknown"
    return {
        SW_SHOWMINIMIZED: "minimized",
        SW_MAXIMIZE: "maximized",
    }.get(int(placement.showCmd), "normal")


def focus_window(hwnd: int) -> bool:
    """Bring a window forward.

    Windows refuses SetForegroundWindow from a process that does not own the
    foreground, by design, to stop applications stealing focus. The restore +
    SetForegroundWindow pair is the polite sequence that works in the common
    case; the RETURN VALUE OF THE REAL CALL is what gets reported, so a refusal
    surfaces as a failure rather than being narrated as success.
    """
    user32 = _user32()
    if is_iconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    return bool(user32.SetForegroundWindow(hwnd))


def foreground_window() -> int:
    return int(_user32().GetForegroundWindow() or 0)


def post_close(hwnd: int) -> bool:
    """Ask a window to close, the way clicking its X does.

    WM_CLOSE is a REQUEST. The application may show "save your work?", it may
    ignore it, it may refuse. That is the entire reason this is the mechanism:
    a graceful close preserves the user's unsaved data, and whether the window
    actually went away is then verified by observation rather than assumed.

    There is deliberately no process-termination fallback anywhere in PC
    Control. If an application declines to close, the honest answer is that it
    declined -- not to kill it and report success.
    """
    return bool(_user32().PostMessageW(hwnd, WM_CLOSE, 0, 0))


def screen_size() -> tuple[int, int]:
    user32 = _user32()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


# --------------------------------------------------------------------------
#  shell32 / launching and opening
# --------------------------------------------------------------------------

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_FLAG_NO_UI = 0x00000400
SEE_MASK_NOASYNC = 0x00000100


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def shell_execute(path: str, *, verb: str = "open", parameters: str | None = None,
                  working_dir: str | None = None) -> int:
    """Open one target through the shell and return the created PID (0 if none).

    ``ShellExecuteExW`` takes the file and its parameters as SEPARATE typed
    fields. There is no command line to quote and nothing for a shell to
    re-parse, which is precisely why this is used instead of
    ``subprocess.run(..., shell=True)``. It is also what makes `.lnk` shortcuts
    work: resolving and launching them is the shell's job.

    The PID comes back through SEE_MASK_NOCLOSEPROCESS, so the caller can
    verify that something really started instead of trusting a return code.
    """
    _require_windows()
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI | SEE_MASK_NOASYNC
    info.hwnd = None
    info.lpVerb = verb
    info.lpFile = path
    info.lpParameters = parameters
    info.lpDirectory = working_dir
    info.nShow = SW_SHOWNORMAL

    if not ctypes.windll.shell32.ShellExecuteExW(byref(info)):
        raise OSError(ctypes.get_last_error() or 0, "ShellExecuteExW failed")

    pid = 0
    if info.hProcess:
        pid = int(ctypes.windll.kernel32.GetProcessId(info.hProcess) or 0)
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return pid


def resolve_shortcut(lnk_path: str) -> str | None:
    """Read the target an .lnk points at, via IShellLink. No shell involved.

    Used only to TELL THE USER what a Start-Menu entry actually launches, and
    to decide whether a shortcut resolves to something plausible. Launching
    still goes through ShellExecuteExW on the .lnk itself.
    """
    _require_windows()
    try:
        CLSID_ShellLink = _guid("{00021401-0000-0000-C000-000000000046}")
        IID_IShellLinkW = _guid("{000214F9-0000-0000-C000-000000000046}")
        IID_IPersistFile = _guid("{0000010B-0000-0000-C000-000000000046}")

        with _com_apartment():
            link = c_void_p()
            if ctypes.windll.ole32.CoCreateInstance(
                    byref(CLSID_ShellLink), None, 1, byref(IID_IShellLinkW), byref(link)):
                return None
            try:
                persist = c_void_p()
                if _vcall(link, 0, (POINTER(_GUID), POINTER(c_void_p)),
                          byref(IID_IPersistFile), byref(persist)):
                    return None
                try:
                    # IPersistFile::Load(pszFileName, dwMode=STGM_READ)
                    if _vcall(persist, 5, (wintypes.LPCWSTR, ctypes.c_ulong), lnk_path, 0):
                        return None
                    buffer = ctypes.create_unicode_buffer(1024)
                    # IShellLinkW::GetPath(pszFile, cch, pfd, fFlags)
                    if _vcall(link, 3, (wintypes.LPWSTR, ctypes.c_int, c_void_p, ctypes.c_uint),
                              buffer, 1024, None, 0):
                        return None
                    return buffer.value or None
                finally:
                    _release(persist)
            finally:
                _release(link)
    except Exception:
        logger.debug("could not resolve shortcut %s", lnk_path, exc_info=True)
        return None


# --------------------------------------------------------------------------
#  ole32 / COM plumbing for the audio endpoint
# --------------------------------------------------------------------------


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    _require_windows()
    guid = _GUID()
    if ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(guid)):
        raise OSError(f"invalid GUID {text}")
    return guid


def _vcall(interface: c_void_p, slot: int, argtypes: tuple, *args) -> int:
    """Call vtable entry ``slot`` on a COM interface pointer. Returns the HRESULT."""
    vtable = ctypes.cast(interface, POINTER(POINTER(c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
    return int(prototype(vtable[slot])(interface, *args))


def _release(interface: c_void_p) -> None:
    """IUnknown::Release, slot 2. Every interface acquired here must be released."""
    if interface:
        try:
            _vcall(interface, 2, ())
        except Exception:
            logger.debug("COM release failed", exc_info=True)


class _com_apartment:
    """CoInitialize/CoUninitialize around a block.

    Tool handlers run on the shared worker pool, so a thread may never have
    initialised COM. RPC_E_CHANGED_MODE means the thread is already in a
    different apartment, which is fine -- COM is usable either way, we simply
    must not uninitialise a thread we did not initialise.
    """

    def __enter__(self):
        self._owned = False
        hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)   # APARTMENTTHREADED
        self._owned = hr in (0, 1)                            # S_OK / S_FALSE
        return self

    def __exit__(self, *_exc):
        if self._owned:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass
        return False


CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

# vtable slots, counted from IUnknown (QueryInterface 0, AddRef 1, Release 2).
_SLOT_ENUM_GET_DEFAULT_ENDPOINT = 4          # IMMDeviceEnumerator
_SLOT_DEVICE_ACTIVATE = 3                    # IMMDevice
_SLOT_VOL_SET_SCALAR = 7                     # IAudioEndpointVolume
_SLOT_VOL_GET_SCALAR = 9
_SLOT_VOL_SET_MUTE = 14
_SLOT_VOL_GET_MUTE = 15

_E_RENDER = 0
_E_CONSOLE = 0
_CLSCTX_ALL = 23


class AudioEndpoint:
    """The default playback device's master volume. Context manager."""

    def __enter__(self) -> "AudioEndpoint":
        _require_windows()
        self._apartment = _com_apartment().__enter__()
        self._enumerator = c_void_p()
        self._device = c_void_p()
        self._volume = c_void_p()
        try:
            clsid, iid = _guid(CLSID_MMDeviceEnumerator), _guid(IID_IMMDeviceEnumerator)
            if ctypes.windll.ole32.CoCreateInstance(
                    byref(clsid), None, _CLSCTX_ALL, byref(iid), byref(self._enumerator)):
                raise OSError("could not create the audio device enumerator")
            if _vcall(self._enumerator, _SLOT_ENUM_GET_DEFAULT_ENDPOINT,
                      (c_int, c_int, POINTER(c_void_p)),
                      _E_RENDER, _E_CONSOLE, byref(self._device)):
                raise OSError("no default audio playback device")
            volume_iid = _guid(IID_IAudioEndpointVolume)
            if _vcall(self._device, _SLOT_DEVICE_ACTIVATE,
                      (POINTER(_GUID), c_int, c_void_p, POINTER(c_void_p)),
                      byref(volume_iid), _CLSCTX_ALL, None, byref(self._volume)):
                raise OSError("could not open the audio endpoint volume")
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_exc):
        for interface in ("_volume", "_device", "_enumerator"):
            _release(getattr(self, interface, None))
            setattr(self, interface, None)
        try:
            self._apartment.__exit__(None, None, None)
        except Exception:
            pass
        return False

    def get_level(self) -> float:
        """Master volume as 0.0-1.0."""
        value = c_float()
        if _vcall(self._volume, _SLOT_VOL_GET_SCALAR, (POINTER(c_float),), byref(value)):
            raise OSError("could not read the volume level")
        return float(value.value)

    def set_level(self, scalar: float) -> None:
        clamped = max(0.0, min(1.0, float(scalar)))
        if _vcall(self._volume, _SLOT_VOL_SET_SCALAR, (c_float, c_void_p), clamped, None):
            raise OSError("could not set the volume level")

    def get_mute(self) -> bool:
        value = c_int()
        if _vcall(self._volume, _SLOT_VOL_GET_MUTE, (POINTER(c_int),), byref(value)):
            raise OSError("could not read the mute state")
        return bool(value.value)

    def set_mute(self, muted: bool) -> None:
        if _vcall(self._volume, _SLOT_VOL_SET_MUTE, (c_int, c_void_p), 1 if muted else 0, None):
            raise OSError("could not change the mute state")


__all__ = [
    "IS_WINDOWS",
    "SW_MAXIMIZE",
    "SW_MINIMIZE",
    "SW_RESTORE",
    "AudioEndpoint",
    "WindowsUnavailable",
    "enum_top_level_windows",
    "focus_window",
    "foreground_window",
    "is_cloaked",
    "is_iconic",
    "is_window",
    "is_window_visible",
    "is_zoomed",
    "post_close",
    "resolve_shortcut",
    "screen_size",
    "shell_execute",
    "show_window",
    "window_class",
    "window_ex_style",
    "window_owner",
    "window_pid",
    "window_placement_state",
    "window_title",
]
