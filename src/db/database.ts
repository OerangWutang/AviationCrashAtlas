/**
 * SQLite database for BFF-owned human-identity data.
 *
 * Uses Node.js built-in node:sqlite (Node 22+).
 * The Atlas FastAPI core owns evidence/claims/conflicts in PostgreSQL.
 * The BFF owns ONLY: human users, sessions, and request-access leads.
 */

import { DatabaseSync } from "node:sqlite";
import { getConfig } from "../config.js";

let _db: DatabaseSync | null = null;

export function getDb(): DatabaseSync {
  if (_db) return _db;
  const { dbPath } = getConfig();
  _db = new DatabaseSync(dbPath);
  _db.exec("PRAGMA journal_mode = WAL");
  _db.exec("PRAGMA foreign_keys = ON");
  return _db;
}

/** Close and reset (tests only). */
export function _closeDb(): void {
  if (_db) {
    try {
      _db.close();
    } catch {
      // Connection may already be closed by another test suite
    }
    _db = null;
  }
}

export function runMigrations(): void {
  const db = getDb();

  db.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id               TEXT    PRIMARY KEY,
      email            TEXT    NOT NULL UNIQUE,
      password_hash    TEXT    NOT NULL,
      role             TEXT    NOT NULL CHECK (role IN ('analyst', 'reviewer', 'admin')),
      atlas_user_id    TEXT    NOT NULL,
      encrypted_api_key TEXT   NOT NULL,
      is_active        INTEGER NOT NULL DEFAULT 1,
      created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
      updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS sessions (
      id         TEXT    PRIMARY KEY,
      user_id    TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT    NOT NULL DEFAULT (datetime('now')),
      expires_at TEXT    NOT NULL,
      ip         TEXT,
      user_agent TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

    CREATE TABLE IF NOT EXISTS request_access_leads (
      id               TEXT    PRIMARY KEY,
      name             TEXT    NOT NULL,
      email            TEXT    NOT NULL,
      organization     TEXT    NOT NULL,
      role_type        TEXT    NOT NULL,
      organization_type TEXT   NOT NULL,
      use_case         TEXT    NOT NULL,
      message          TEXT,
      created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
    );
  `);
}
