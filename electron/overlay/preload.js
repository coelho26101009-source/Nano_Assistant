/**
 * The voice overlay's entire API: receive a state, draw it.
 *
 * The overlay is a status light. It has no controls, no backend access and no
 * way to start, stop or influence a voice turn — it only renders what the main
 * process tells it the backend is really doing. One inbound channel, nothing
 * outbound, so there is nothing here to misuse.
 */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('nanoOverlay', Object.freeze({
  onState: (handler) => {
    if (typeof handler !== 'function') return;
    ipcRenderer.on('nano:overlay-state', (_event, view) => {
      try {
        handler(view);
      } catch (err) {
        console.error('[nano-overlay] render failed:', err);
      }
    });
  },
}));
