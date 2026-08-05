/**
 * H.E.L.I.O.S. Preload Script
 * Bridge segura entre Electron e o frontend
 */

const { contextBridge, ipcRenderer } = require('electron');

// Expõe API segura ao frontend para controlo da janela
contextBridge.exposeInMainWorld('heliosApp', {
  minimize:  () => ipcRenderer.send('window-minimize'),
  maximize:  () => ipcRenderer.send('window-maximize'),
  hide:      () => ipcRenderer.send('window-hide'),
  close:     () => ipcRenderer.send('window-close'),
  getAutoLaunch: ()        => ipcRenderer.invoke('autolaunch-get'),
  setAutoLaunch: (enabled) => ipcRenderer.invoke('autolaunch-set', enabled),
  isElectron: true,
});
