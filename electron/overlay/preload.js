/**
 * The island's entire API.
 *
 * Inbound: one state to draw. Outbound: three fixed signals, none carrying
 * data the page can choose beyond a closed set --
 *   setPointer(bool)  the pointer is over the pill, so the window should catch
 *                     clicks there (it is click-through everywhere else);
 *   open()            the pill was clicked: bring NANO to the front;
 *   media(action)     a music player button: 'previous', 'play_pause' or
 *                     'next'. Anything else is dropped here, and again in
 *                     the main process, and again in the backend.
 * There is no way to start, stop, approve or influence anything else from
 * here. An approval shown on the island is answered in NANO, never here.
 */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

const MEDIA_ACTIONS = new Set(['previous', 'play_pause', 'next']);

contextBridge.exposeInMainWorld('nanoOverlay', Object.freeze({
  onState: (handler) => {
    if (typeof handler !== 'function') return;
    ipcRenderer.on('nano:overlay-state', (_event, view) => {
      try {
        handler(view);
      } catch (err) {
        console.error('[nano-island] render failed:', err);
      }
    });
  },
  setPointer: (inside) => { ipcRenderer.send('nano:island-pointer', inside === true); },
  open: () => { ipcRenderer.send('nano:island-open'); },
  media: (action) => {
    if (typeof action === 'string' && MEDIA_ACTIONS.has(action)) ipcRenderer.send('nano:island-media', action);
  },
}));
