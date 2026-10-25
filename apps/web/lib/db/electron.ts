// Electron IPC data access layer
// Used when running inside Electron (window.api is available)

import type { BoardData, ColumnData, CardData } from '@superapp/shared';

export interface ElectronDb {
  boards: {
    list: () => Promise<BoardData[]>;
    create: (name: string) => Promise<BoardData>;
  };
  columns: {
    list: (boardId: string) => Promise<ColumnData[]>;
  };
  cards: {
    list: (columnId: string) => Promise<CardData[]>;
  };
}

export function getElectronDb(): ElectronDb | null {
  if (typeof window !== 'undefined' && (window as any).api?.db) {
    return (window as any).api.db;
  }
  return null;
}
