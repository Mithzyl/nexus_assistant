import { ipcMain } from 'electron';
import { prisma } from '../db/client';

// --- Input validation helpers ---

function assertNonEmptyString(value: unknown, field: string, maxLen = 100): asserts value is string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new Error(`${field} must be a non-empty string`);
  }
  if (value.trim().length > maxLen) {
    throw new Error(`${field} must be at most ${maxLen} characters`);
  }
}

function assertId(value: unknown, field: string): asserts value is string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new Error(`${field} must be a non-empty string (id)`);
  }
}

// --- Error handling wrapper ---

function wrapHandler<T extends unknown[], R>(
  handler: (...args: T) => Promise<R>,
): (...args: T) => Promise<R> {
  return async (...args: T) => {
    try {
      return await handler(...args);
    } catch (error) {
      console.error('[IPC Error]', error);
      throw new Error(
        error instanceof Error ? error.message : 'Unknown database error',
      );
    }
  };
}

// --- Handler registration ---

export function registerDbHandlers() {
  ipcMain.handle(
    'db:boards:list',
    wrapHandler(async () => {
      return prisma.board.findMany({ include: { columns: true } });
    }),
  );

  ipcMain.handle(
    'db:boards:create',
    wrapHandler(async (_event, name: unknown) => {
      assertNonEmptyString(name, 'Board name');
      return prisma.board.create({ data: { name: name.trim() } });
    }),
  );

  ipcMain.handle(
    'db:columns:list',
    wrapHandler(async (_event, boardId: unknown) => {
      assertId(boardId, 'boardId');
      return prisma.column.findMany({
        where: { boardId },
        include: { cards: true },
      });
    }),
  );

  ipcMain.handle(
    'db:cards:list',
    wrapHandler(async (_event, columnId: unknown) => {
      assertId(columnId, 'columnId');
      return prisma.card.findMany({ where: { columnId } });
    }),
  );
}
