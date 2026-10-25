// Unified data access layer
// Detects environment and routes to Electron IPC or Web stub

import { getElectronDb, type ElectronDb } from './electron';
import { getWebDb, type WebDb } from './web';

type DbClient = ElectronDb | WebDb;

let _db: DbClient | null = null;

export function getDb(): DbClient {
  if (_db) return _db;

  const electronDb = getElectronDb();
  if (electronDb) {
    _db = electronDb;
  } else {
    _db = getWebDb();
  }
  return _db;
}
