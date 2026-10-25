import type { BoardData, ColumnData, CardData } from '@superapp/shared';

export {};

declare global {
  interface Window {
    api?: {
      db: {
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
      };
    };
  }
}
