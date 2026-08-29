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


_dpi_awareness_set = False


def ensure_dpi_awareness() -> None:
    """Declare per-monitor DPI awareness once, before any geometry is read.

    Without it Windows lies to the process about every coordinate on a scaled
    display: GetWindowRect and GetMonitorInfo come back in virtualised pixels,
    so a window "moved to the right half" lands somewhere else entirely. It is
    process-wide and idempotent, so it is done once and then never again --
    calling it repeatedly is harmless but the second call always fails, and a
    failure here must not look like a problem.
    """
    global _dpi_awareness_set
    if _dpi_awareness_set or not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            logger.debug("could not set DPI awareness", exc_info=True)
    _dpi_awareness_set = True


def screen_size() -> tuple[int, int]:
    user32 = _user32()
    ensure_dpi_awareness()
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



# ==========================================================================
#  PC CONTROL V2 -- additional Win32 primitives
#
#  Everything below follows the same rule as everything above: a typed Win32
#  call, never a command line. Each block is grouped by the DLL it talks to, so
#  the whole surface Nano can actually reach is readable in one pass.
# ==========================================================================

# --------------------------------------------------------------------------
#  user32 / monitors and window geometry
# --------------------------------------------------------------------------

MONITORINFOF_PRIMARY = 0x00000001

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

WS_EX_TOPMOST = 0x00000008

MONITOR_DEFAULTTONEAREST = 2


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


def _rect_tuple(rect) -> tuple[int, int, int, int]:
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) in virtual-desktop coordinates."""
    user32 = _user32()
    ensure_dpi_awareness()
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), byref(rect)):
        raise OSError("GetWindowRect failed")
    return _rect_tuple(rect)


def set_window_position(hwnd: int, x: int, y: int, width: int, height: int) -> bool:
    """Move and size one window. The caller has already validated the geometry.

    SWP_NOZORDER | SWP_NOACTIVATE on purpose: moving a window must not also
    raise it above everything else or steal the user's keyboard focus.
    """
    user32 = _user32()
    ensure_dpi_awareness()
    return bool(user32.SetWindowPos(
        wintypes.HWND(hwnd), None, int(x), int(y), int(width), int(height),
        SWP_NOZORDER | SWP_NOACTIVATE,
    ))


def set_window_topmost(hwnd: int, topmost: bool) -> bool:
    user32 = _user32()
    insert_after = HWND_TOPMOST if topmost else HWND_NOTOPMOST
    return bool(user32.SetWindowPos(
        wintypes.HWND(hwnd), wintypes.HWND(insert_after), 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    ))


def is_window_topmost(hwnd: int) -> bool:
    """Read the style back rather than trusting SetWindowPos's return value."""
    return bool(window_ex_style(hwnd) & WS_EX_TOPMOST)


def enum_monitors() -> list[dict]:
    """Every physical display, with its full rect and its WORK AREA.

    The work area excludes the taskbar, which is why snapping uses it: a window
    snapped to the monitor rect sits partly underneath the taskbar.
    """
    user32 = _user32()
    ensure_dpi_awareness()
    found: list[dict] = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC,
                              POINTER(wintypes.RECT), wintypes.LPARAM)

    def _thunk(handle, _hdc, _lprect, _lparam):
        try:
            info = _MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
            if user32.GetMonitorInfoW(handle, byref(info)):
                found.append({
                    "handle": int(handle),
                    "device": str(info.szDevice),
                    "bounds": _rect_tuple(info.rcMonitor),
                    "work_area": _rect_tuple(info.rcWork),
                    "primary": bool(int(info.dwFlags) & MONITORINFOF_PRIMARY),
                })
        except Exception:
            logger.debug("monitor enumeration entry failed", exc_info=True)
        return True

    user32.EnumDisplayMonitors(None, None, proc(_thunk), 0)
    return found


def monitor_handle_for_window(hwnd: int) -> int:
    user32 = _user32()
    _configure_handle_signatures()
    return int(user32.MonitorFromWindow(wintypes.HWND(hwnd),
                                        MONITOR_DEFAULTTONEAREST) or 0)


# --------------------------------------------------------------------------
#  user32 / SendInput
#
#  THE INPUT SURFACE IS TYPED, NOT A STRING.
#
#  There is no function here that takes "a key sequence". Text is sent as
#  UNICODE CHARACTERS (KEYEVENTF_UNICODE), which needs no scan-code table and
#  cannot express a chord at all; a chord is sent by `press_chord`, which takes
#  a virtual-key code plus modifier codes that the CALLER already checked
#  against its own allow-list. A caller holding a key Nano does not support has
#  nowhere to put it.
# --------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MAPVK_VK_TO_VSC = 0

#: Virtual keys that live on the extended part of the keyboard. Injected
#: without KEYEVENTF_EXTENDEDKEY they arrive as their NUMPAD twins, which is a
#: different key to any application that tells the two apart -- an arrow that
#: moves nothing, a Delete that does not delete.
_EXTENDED_KEYS = frozenset({
    0x21, 0x22, 0x23, 0x24,          # PageUp, PageDown, End, Home
    0x25, 0x26, 0x27, 0x28,          # Left, Up, Right, Down
    0x2D, 0x2E,                      # Insert, Delete
    0x5B, 0x5C,                      # Left/Right Win
    0x90, 0xA3, 0xA5, 0x6F, 0x2C,    # NumLock, RCtrl, RAlt, Divide, PrintScreen
})

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send(events: list) -> int:
    """Inject a batch of input events. Returns how many Windows accepted."""
    user32 = _user32()
    if not events:
        return 0
    array = (_INPUT * len(events))(*events)
    return int(user32.SendInput(len(events), array, ctypes.sizeof(_INPUT)))


def _utf16_units(character: str) -> list[int]:
    encoded = character.encode("utf-16-le", "ignore")
    return [int.from_bytes(encoded[i:i + 2], "little")
            for i in range(0, len(encoded), 2)]


def _key_event(vk: int, *, up: bool, unicode_char: int | None = None):
    """One keyboard event, shaped the way a real keyboard would send it.

    THE SCAN CODE IS NOT OPTIONAL. An event with `wScan = 0` is delivered and
    accepted, and then ignored by any application that matches its accelerators
    on the scan code rather than the virtual key -- which the XAML input stack
    behind Notepad, Settings and the Store apps does. That failure is silent:
    SendInput reports success because the event really was injected.

    Unicode events are different and are left alone: KEYEVENTF_UNICODE carries
    the character itself, and `wScan` is where the character goes.
    """
    event = _INPUT()
    event.type = INPUT_KEYBOARD
    flags = KEYEVENTF_KEYUP if up else 0
    if unicode_char is not None:
        event.ki = _KEYBDINPUT(0, unicode_char, flags | KEYEVENTF_UNICODE, 0, 0)
        return event

    code = int(vk)
    if code in _EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    scan = 0
    if IS_WINDOWS:
        try:
            scan = int(ctypes.windll.user32.MapVirtualKeyW(code, MAPVK_VK_TO_VSC) or 0)
        except Exception:
            logger.debug("could not map virtual key %s to a scan code", code, exc_info=True)
    event.ki = _KEYBDINPUT(code, scan, flags, 0, 0)
    return event


def type_unicode(text: str) -> int:
    """Send ``text`` to whatever holds keyboard focus, character by character.

    KEYEVENTF_UNICODE sends the CHARACTER, not a key. That matters twice over:
    accented Portuguese arrives correctly whatever the active layout is, and
    there is no scan code anywhere for a caller to supply. Characters outside
    the basic plane are sent as their two UTF-16 code units, which is what
    Windows expects.

    Returns how many events Windows accepted, so the caller can report a real
    number rather than assuming the text landed.
    """
    _require_windows()
    events = []
    for character in text:
        for unit in _utf16_units(character):
            events.append(_key_event(0, up=False, unicode_char=unit))
            events.append(_key_event(0, up=True, unicode_char=unit))
    return _send(events)


def press_chord(vk: int, modifiers: tuple = ()) -> int:
    """Press ``modifiers`` + ``vk``, then release them in reverse order.

    Both the key and the modifiers are integers the caller resolved from its
    own allow-list. Nothing here parses a string into keystrokes.
    """
    _require_windows()
    events = []
    for modifier in modifiers:
        events.append(_key_event(modifier, up=False))
    events.append(_key_event(vk, up=False))
    events.append(_key_event(vk, up=True))
    for modifier in reversed(modifiers):
        events.append(_key_event(modifier, up=True))
    return _send(events)


def scroll_wheel(clicks: int, horizontal: bool = False) -> int:
    """Scroll the wheel wherever the pointer already is.

    No coordinates: this cannot move the pointer and cannot click anything.
    """
    _require_windows()
    event = _INPUT()
    event.type = INPUT_MOUSE
    delta = int(clicks) * WHEEL_DELTA
    flags = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    event.mi = _MOUSEINPUT(0, 0, ctypes.c_uint32(delta).value, flags, 0, 0)
    return _send([event])


def cursor_position() -> tuple[int, int]:
    user32 = _user32()
    ensure_dpi_awareness()
    point = wintypes.POINT()
    if not user32.GetCursorPos(byref(point)):
        raise OSError("GetCursorPos failed")
    return int(point.x), int(point.y)


# --------------------------------------------------------------------------
#  user32 + kernel32 / clipboard
# --------------------------------------------------------------------------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_signatures_configured = False


def _configure_handle_signatures() -> None:
    """Declare the return types of every handle-returning call used here.

    ctypes defaults an undeclared restype to `c_int`, which TRUNCATES a 64-bit
    handle to 32 bits. The truncated value is still a plausible-looking number,
    so the failure is not a type error -- it is an access violation the first
    time the handle is dereferenced, which is exactly what GetClipboardData did
    before this existed. Declared once, at first use.
    """
    global _signatures_configured
    if _signatures_configured or not IS_WINDOWS:
        return
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32

    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]

    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    _signatures_configured = True


def _open_clipboard(attempts: int = 8) -> bool:
    """Take the clipboard, retrying briefly.

    The clipboard is a single global resource and another process may hold it
    for a few milliseconds. Retrying is the documented approach; failing on the
    first refusal would make every clipboard tool flaky for no reason.
    """
    import time as _time

    user32 = _user32()
    for _ in range(max(1, attempts)):
        if user32.OpenClipboard(None):
            return True
        _time.sleep(0.03)
    return False


def clipboard_read_text(limit: int) -> str | None:
    """The clipboard's text, or None when it holds something that is not text.

    No other format is touched. An image or a file list on the clipboard is
    simply "not text" -- not something to describe, convert, or copy elsewhere.
    """
    _require_windows()
    _configure_handle_signatures()
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    if not _open_clipboard():
        raise OSError("could not open the clipboard")
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)[:limit]
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_write_text(text: str) -> bool:
    _require_windows()
    _configure_handle_signatures()
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    payload = str(text)
    size = (len(payload) + 1) * ctypes.sizeof(ctypes.c_wchar)
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        raise OSError("could not allocate clipboard memory")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise OSError("could not lock clipboard memory")
    try:
        ctypes.memmove(pointer, ctypes.create_unicode_buffer(payload), size)
    finally:
        kernel32.GlobalUnlock(handle)

    if not _open_clipboard():
        kernel32.GlobalFree(handle)
        raise OSError("could not open the clipboard")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        # Ownership of the block passes to the clipboard on success; freeing it
        # here would leave the clipboard pointing at released memory.
        return True
    finally:
        user32.CloseClipboard()


def clipboard_clear() -> bool:
    _require_windows()
    _configure_handle_signatures()
    user32 = ctypes.windll.user32
    if not _open_clipboard():
        raise OSError("could not open the clipboard")
    try:
        return bool(user32.EmptyClipboard())
    finally:
        user32.CloseClipboard()


# --------------------------------------------------------------------------
#  dxva2 / monitor brightness (the Monitor Configuration API)
#
#  WHY THIS AND NOT A GAMMA RAMP. SetDeviceGammaRamp is the trick usually found
#  in "set brightness from Python" answers. It does not change brightness at
#  all: it washes out the colours of everything on screen and leaves the
#  panel's backlight exactly where it was. This is the documented Win32 API for
#  the job, it reports the monitor's OWN minimum and maximum so nothing is
#  assumed about the hardware, and on a display that does not implement DDC/CI
#  it returns false -- which is reported as `unsupported` rather than papered
#  over with a fake success.
# --------------------------------------------------------------------------


class _PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("hPhysicalMonitor", wintypes.HANDLE),
                ("szPhysicalMonitorDescription", ctypes.c_wchar * 128)]


class _physical_monitors:
    """The physical monitors behind one HMONITOR. Always destroyed again."""

    def __init__(self, monitor_handle: int):
        self._handle = monitor_handle
        self._array = None
        self._count = 0

    def __enter__(self):
        _require_windows()
        dxva2 = ctypes.windll.dxva2
        count = wintypes.DWORD()
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(
                wintypes.HMONITOR(self._handle), byref(count)) or count.value == 0:
            raise OSError("no physical monitor behind this display")
        array = (_PHYSICAL_MONITOR * count.value)()
        if not dxva2.GetPhysicalMonitorsFromHMONITOR(
                wintypes.HMONITOR(self._handle), count.value, array):
            raise OSError("could not open the physical monitor")
        self._array, self._count = array, count.value
        return self

    def __exit__(self, *_exc):
        if self._array is not None:
            try:
                ctypes.windll.dxva2.DestroyPhysicalMonitors(self._count, self._array)
            except Exception:
                logger.debug("DestroyPhysicalMonitors failed", exc_info=True)
        self._array = None
        return False

    @property
    def first(self):
        return self._array[0]

    @property
    def description(self) -> str:
        return str(self._array[0].szPhysicalMonitorDescription)


def monitor_brightness(monitor_handle: int) -> tuple[int, int, int] | None:
    """(minimum, current, maximum) in the monitor's own units, or None."""
    try:
        with _physical_monitors(monitor_handle) as monitors:
            low, current, high = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
            if not ctypes.windll.dxva2.GetMonitorBrightness(
                    monitors.first.hPhysicalMonitor,
                    byref(low), byref(current), byref(high)):
                return None
            return int(low.value), int(current.value), int(high.value)
    except OSError:
        return None
    except Exception:
        logger.debug("brightness read failed", exc_info=True)
        return None


def set_monitor_brightness(monitor_handle: int, value: int) -> bool:
    try:
        with _physical_monitors(monitor_handle) as monitors:
            return bool(ctypes.windll.dxva2.SetMonitorBrightness(
                monitors.first.hPhysicalMonitor, wintypes.DWORD(int(value))))
    except OSError:
        return False
    except Exception:
        logger.debug("brightness write failed", exc_info=True)
        return False


# --------------------------------------------------------------------------
#  shell32 / the Recycle Bin
#
#  RECYCLING IS NOT DELETING. SHFileOperationW with FOF_ALLOWUNDO is the
#  shell's own "send to Recycle Bin", so the user gets the file back from
#  Explorer exactly as if they had pressed Delete themselves. FOF_WANTNUKEWARNING
#  is set deliberately: when an item CANNOT be recycled -- too large for the
#  bin, or on a volume with no bin -- Windows asks the user rather than
#  silently destroying it. Nano never makes that call on their behalf, and
#  there is no unlink() anywhere in PC Control to fall back to.
# --------------------------------------------------------------------------

FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_int64),
                ("i64NumItems", ctypes.c_int64)]


def recycle_bin_items(root: str) -> int | None:
    """How many items the Recycle Bin holds for a drive, or None if unreadable.

    This is the VERIFICATION for a recycle: if the count did not go up, the
    file did not land in the bin, whatever the operation's return code said.
    """
    _require_windows()
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    if ctypes.windll.shell32.SHQueryRecycleBinW(ctypes.c_wchar_p(root), byref(info)) != 0:
        return None
    return int(info.i64NumItems)


def shell_recycle(path: str) -> tuple[int, bool]:
    """Send one path to the Recycle Bin. Returns (result_code, aborted)."""
    _require_windows()
    operation = _SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = FO_DELETE
    # pFrom is a DOUBLE-null-terminated list of names, not a plain string.
    operation.pFrom = f"{path}\0\0"
    operation.pTo = None
    operation.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI
                        | FOF_SILENT | FOF_WANTNUKEWARNING)
    operation.fAnyOperationsAborted = 0
    code = int(ctypes.windll.shell32.SHFileOperationW(byref(operation)))
    return code, bool(operation.fAnyOperationsAborted)


# --------------------------------------------------------------------------
#  user32 / powrprof / advapi32 -- session and power
#
#  NOTHING HERE IS FORCED. ExitWindowsEx is called WITHOUT EWX_FORCE, so an
#  application holding unsaved work can veto the shutdown and show its own
#  dialog. Forcing would be a data-loss primitive, and PC Control does not own
#  one. There is likewise no countdown and no scheduled variant: the action
#  happens when the user has just approved it, or not at all.
# --------------------------------------------------------------------------

EWX_LOGOFF = 0x00000000
EWX_SHUTDOWN = 0x00000001
EWX_REBOOT = 0x00000002
EWX_POWEROFF = 0x00000008

SHTDN_REASON_MAJOR_OTHER = 0x00000000
SHTDN_REASON_MINOR_OTHER = 0x00000000
SHTDN_REASON_FLAG_PLANNED = 0x80000000

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002
ERROR_NOT_ALL_ASSIGNED = 1300


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", ctypes.c_long)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD),
                ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


def enable_shutdown_privilege() -> bool:
    """Enable SeShutdownPrivilege on this process token.

    ExitWindowsEx fails with ERROR_ACCESS_DENIED without it. Every interactive
    user already HOLDS this privilege; enabling it grants nothing they did not
    have, which is why it can be done here rather than being a separate
    escalation step.
    """
    _require_windows()
    _configure_handle_signatures()
    advapi32, kernel32 = ctypes.windll.advapi32, ctypes.windll.kernel32
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                     byref(token)):
        return False
    try:
        luid = _LUID()
        if not advapi32.LookupPrivilegeValueW(None, "SeShutdownPrivilege", byref(luid)):
            return False
        privileges = _TOKEN_PRIVILEGES()
        privileges.PrivilegeCount = 1
        privileges.Privileges[0].Luid = luid
        privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        if not advapi32.AdjustTokenPrivileges(token, False, byref(privileges),
                                              0, None, None):
            return False
        # AdjustTokenPrivileges reports success even when it changed nothing,
        # so the real answer is in the last error.
        return kernel32.GetLastError() != ERROR_NOT_ALL_ASSIGNED
    finally:
        kernel32.CloseHandle(token)


def lock_workstation() -> bool:
    return bool(_user32().LockWorkStation())


def suspend_system() -> bool:
    """Sleep. Never hibernate, never forced."""
    _require_windows()
    return bool(ctypes.windll.powrprof.SetSuspendState(0, 0, 0))


def exit_windows(flags: int) -> bool:
    """Log off, restart or shut down. The caller supplies a constant, not text."""
    _require_windows()
    enable_shutdown_privilege()
    reason = (SHTDN_REASON_MAJOR_OTHER | SHTDN_REASON_MINOR_OTHER
              | SHTDN_REASON_FLAG_PLANNED)
    return bool(ctypes.windll.user32.ExitWindowsEx(
        wintypes.UINT(int(flags)), wintypes.DWORD(reason)))


# --------------------------------------------------------------------------
#  wininet / connectivity
# --------------------------------------------------------------------------

INTERNET_CONNECTION_MODEM = 0x01
INTERNET_CONNECTION_LAN = 0x02
INTERNET_CONNECTION_PROXY = 0x04


def internet_connection() -> tuple[bool, int]:
    """(connected, flags). A local query -- it sends no traffic anywhere."""
    _require_windows()
    flags = wintypes.DWORD()
    connected = bool(ctypes.windll.wininet.InternetGetConnectedState(byref(flags), 0))
    return connected, int(flags.value)


# --------------------------------------------------------------------------
#  user32 / capturing a single window
# --------------------------------------------------------------------------

PW_RENDERFULLCONTENT = 0x00000002


def print_window(hwnd: int, hdc: int) -> bool:
    """Ask a window to render itself into a device context.

    PW_RENDERFULLCONTENT captures modern (DirectComposition) windows that a
    plain screen BitBlt would miss, and it works while the window is partly
    covered. It is allowed to fail; the caller then falls back to copying the
    window's rectangle off the screen, and says which one it used.
    """
    return bool(_user32().PrintWindow(wintypes.HWND(hwnd), wintypes.HDC(hdc),
                                      PW_RENDERFULLCONTENT))


__all__ = [
    "CF_UNICODETEXT",
    "EWX_LOGOFF",
    "EWX_POWEROFF",
    "EWX_REBOOT",
    "EWX_SHUTDOWN",
    "INTERNET_CONNECTION_LAN",
    "INTERNET_CONNECTION_MODEM",
    "INTERNET_CONNECTION_PROXY",
    "IS_WINDOWS",
    "SW_MAXIMIZE",
    "SW_MINIMIZE",
    "SW_RESTORE",
    "WHEEL_DELTA",
    "WS_EX_TOPMOST",
    "AudioEndpoint",
    "WindowsUnavailable",
    "clipboard_clear",
    "clipboard_read_text",
    "clipboard_write_text",
    "cursor_position",
    "enable_shutdown_privilege",
    "ensure_dpi_awareness",
    "enum_monitors",
    "enum_top_level_windows",
    "exit_windows",
    "focus_window",
    "foreground_window",
    "internet_connection",
    "is_cloaked",
    "is_iconic",
    "is_window",
    "is_window_topmost",
    "is_window_visible",
    "is_zoomed",
    "lock_workstation",
    "monitor_brightness",
    "monitor_handle_for_window",
    "post_close",
    "press_chord",
    "print_window",
    "recycle_bin_items",
    "resolve_shortcut",
    "screen_size",
    "scroll_wheel",
    "set_monitor_brightness",
    "set_window_position",
    "set_window_topmost",
    "shell_execute",
    "shell_recycle",
    "show_window",
    "suspend_system",
    "type_unicode",
    "window_class",
    "window_ex_style",
    "window_owner",
    "window_pid",
    "window_placement_state",
    "window_rect",
    "window_title",
]
