import { app, BrowserWindow } from 'electron';
import path from 'path';
import { registerDbHandlers } from './ipc/db';

let mainWindow: BrowserWindow | null = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    title: 'SuperApp',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Dev: load Next.js dev server
  const isDev = !app.isPackaged;
  if (isDev) {
    // The mobile client now owns `/`; the legacy board keeps its own route.
    mainWindow.loadURL('http://localhost:3000/board');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '../../web/out/board.html'));
  }
}

app.whenReady().then(() => {
  registerDbHandlers();
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
