const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('soundmeDesktop', {
  isDesktop: true,
  getVersion: () => ipcRenderer.invoke('app:version'),
  getBackendStatus: () => ipcRenderer.invoke('backend:status'),
  openExternal: (url) => ipcRenderer.invoke('shell:openExternal', url),
  showItemInFolder: (path) => ipcRenderer.invoke('shell:showItemInFolder', path),
  onStatusUpdate: (callback) => {
    const handler = (_event, data) => callback(data);
    ipcRenderer.on('backend:status-update', handler);
    return () => ipcRenderer.removeListener('backend:status-update', handler);
  },
});
