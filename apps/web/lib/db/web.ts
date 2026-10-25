// Web (browser) data access stub
// Phase 1: returns empty data. Phase 2: IndexedDB implementation.

import type { BoardData, ColumnData, CardData } from '@superapp/shared';

export interface WebDb {
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

export function getWebDb(): WebDb {
  return {
    boards: {
      list: async () => [],
      create: async () => {
        throw new Error('boards.create is not supported in web mode');
      },
    },
    columns: {
      list: async () => [],
    },
    cards: {
      list: async () => [],
    },
  };
}
