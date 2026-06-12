/**
 * PostgreSQL database setup for the BFF.
 *
 * Replaces node:sqlite in production.  The local dev path still uses
 * SQLite (./bff.db) so tests and one-box dev don't need a PG server.
 *
 * Activated by setting BFF_DATABASE_URL to a PostgreSQL connection
 * string.  When unset, the BFF falls back to node:sqlite.
 */

import pg from "pg";

let _pool: pg.Pool | null = null;

export function getPgPool(): pg.Pool {
  if (!_pool) throw new Error("PG pool not initialized. Call createPgPool() first.");
  return _pool;
}

export async function createPgPool(connectionString: string): Promise<pg.Pool> {
  const pool = new pg.Pool({
    connectionString,
    max: 10,
    idleTimeoutMillis: 30000,
  });

  // Verify connection
  const client = await pool.connect();
  try {
    await client.query("SELECT 1");
  } finally {
    client.release();
  }

  await runMigrations(pool);
  _pool = pool;
  return pool;
}

async function runMigrations(pool: pg.Pool): Promise<void> {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS users (
      id          TEXT PRIMARY KEY,
      email       TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      role        TEXT NOT NULL CHECK (role IN ('analyst','reviewer','admin')),
      atlas_user_id TEXT NOT NULL,
      encrypted_api_key TEXT NOT NULL,
      is_active   INTEGER NOT NULL DEFAULT 1,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS sessions (
      id          TEXT PRIMARY KEY,
      user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      expires_at  TIMESTAMPTZ NOT NULL,
      ip          TEXT,
      user_agent  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

    CREATE TABLE IF NOT EXISTS request_access_leads (
      id                TEXT PRIMARY KEY,
      name              TEXT NOT NULL,
      email             TEXT NOT NULL,
      organization      TEXT NOT NULL,
      role_type         TEXT NOT NULL,
      organization_type TEXT NOT NULL,
      use_case          TEXT NOT NULL,
      message           TEXT,
      created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);
}

export async function closePgPool(): Promise<void> {
  if (_pool) {
    await _pool.end();
    _pool = null;
  }
}

/** For tests: allow injecting a test pool. */
export function _setPgPool(pool: pg.Pool): void {
  _pool = pool;
}