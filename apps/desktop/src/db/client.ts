import { PrismaClient } from '@prisma/client';
import { app } from 'electron';
import path from 'path';

let dbPath: string;

if (app.isPackaged) {
  dbPath = path.join(app.getPath('userData'), 'superapp.db');
} else {
  dbPath = path.join(__dirname, '../../web/prisma/dev.db');
}

// Override DATABASE_URL for Prisma
process.env.DATABASE_URL = `file:${dbPath}`;

export const prisma = new PrismaClient();
