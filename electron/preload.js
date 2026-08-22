/**
 * The entire surface the Nano UI is given by the desktop shell.
 *
 * THE RULE: named operations only.
 *
 * Everything here is a fixed verb with a fixed shape. There is deliberately no
 * `invoke(channel, ...args)`, no `send(channel, ...)`, no `on(channel, ...)`,
 * and no object that could be walked back to one. A generic channel is the
 * classic Electron mistake -- it looks like one small convenience and it hands
 * the page every IPC handler the main process will ever register, including the
 * ones added by someone else next year.
 *
 * Nothing here can:
 *   * run a command, a script or a path
 *   * read or write a file
 *   * reach the network
 *   * read a secret
 *   * ask Nano to execute a tool or approve a permission
 *
 * Window control and honest desktop status. That is the whole list. Everything
 * Nano actually *does* still goes over the existing eel bridge and through
 * REQUEST -> POLICY -> PERMISSION -> EXECUTION.
 *
 * Runs with contextIsolation and sandbox enabled, so the page never sees
 * `require`, `process` or any Node primitive.
 */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

/** Subscribe to a main-process push, returning an unsubscribe function. */
function subscribe(channel, handler) {
  if (typeof handler !== 'function') return () => {};
  const listener = (_event, payload) => {
    try {
      handler(payload);
    } catch (err) {
      console.error('[nano-desktop] handler failed:', channel, err);
    }
  };
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

const nanoApp = Object.freeze({
  /** Capability flag. The web build has no window.nanoApp at all. */
  isDesktop: true,

  /* Window controls, for the custom title bar. */
  minimize: () => ipcRenderer.send('nano:minimize'),
  toggleMaximize: () => ipcRenderer.send('nano:toggle-maximize'),
  /** The close button hides to the tray; quitting is explicit and separate. */
  hide: () => ipcRenderer.send('nano:hide'),
  quit: () => ipcRenderer.invoke('nano:quit'),

  /** Maximised / focused / full-screen, for drawing the right glyphs. */
  getWindowState: () => ipcRenderer.invoke('nano:window-state'),
  onWindowState: (handler) => subscribe('nano:window-state', handler),

  /**
   * Real desktop status: the accelerator actually registered, whether that
   * registration succeeded, the effective data directory, and whether this is
   * a packaged build. Measured in the main process; never optimistic.
   */
  getDesktopStatus: () => ipcRenderer.invoke('nano:desktop-status'),
  retryShortcut: () => ipcRenderer.invoke('nano:retry-shortcut'),

  setOverlayEnabled: (enabled) => ipcRenderer.invoke('nano:set-overlay-enabled', Boolean(enabled)),
  setAutoLaunch: (enabled) => ipcRenderer.invoke('nano:set-auto-launch', Boolean(enabled)),
});

contextBridge.exposeInMainWorld('nanoApp', nanoApp);
