import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('api', {
  db: {
    boards: {
      list: () => ipcRenderer.invoke('db:boards:list'),
      create: (name: string) => ipcRenderer.invoke('db:boards:create', name),
    },
    columns: {
      list: (boardId: string) => ipcRenderer.invoke('db:columns:list', boardId),
    },
    cards: {
      list: (columnId: string) => ipcRenderer.invoke('db:cards:list', columnId),
    },
  },
});
