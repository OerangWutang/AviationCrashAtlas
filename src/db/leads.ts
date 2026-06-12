/**
 * Lead repository — auto-detects SQLite vs PostgreSQL backend.
 *
 * When BFF_DATABASE_URL is set, delegates to PG. Otherwise uses SQLite.
 */

import { randomUUID } from "node:crypto";

const USE_PG = !!process.env["BFF_DATABASE_URL"];

export interface RequestAccessLead {
  id: string;
  name: string;
  email: string;
  organization: string;
  roleType: string;
  organizationType: string;
  useCase: string;
  message?: string;
  createdAt: string;
}

export const VALID_ORG_TYPES = new Set([
  "law_firm", "aviation_safety", "airline", "mro", "insurer", "other",
]);

// ── PG backend ──────────────────────────────────────────────────────────────

async function pgCreate(data: Parameters<typeof createLead>[0]): Promise<RequestAccessLead> {
  const { createLeadPg } = await import("./repos-pg.js");
  const id = randomUUID();
  const createdAt = new Date().toISOString();
  await createLeadPg({ ...data, id });
  return { id, ...data, createdAt };
}

async function pgList(limit: number): Promise<RequestAccessLead[]> {
  const { listLeadsPg } = await import("./repos-pg.js");
  return (await listLeadsPg(limit)) as RequestAccessLead[];
}

async function pgCount(): Promise<number> {
  const { countLeadsPg } = await import("./repos-pg.js");
  return await countLeadsPg();
}

// ── SQLite backend ──────────────────────────────────────────────────────────

import { getDb } from "./database.js";

function sqliteCreate(data: Parameters<typeof createLead>[0]): RequestAccessLead {
  const db = getDb();
  const id = randomUUID();
  const createdAt = new Date().toISOString();
  db.prepare(`
    INSERT INTO request_access_leads (id, name, email, organization, role_type, organization_type, use_case, message, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(id, data.name, data.email, data.organization, data.roleType, data.organizationType, data.useCase, data.message ?? null, createdAt);
  return { id, ...data, createdAt };
}

function sqliteList(limit: number): RequestAccessLead[] {
  const db = getDb();
  const rows = db.prepare("SELECT * FROM request_access_leads ORDER BY created_at DESC LIMIT ?").all(limit) as Array<Record<string, unknown>>;
  return rows.map(mapRow);
}

function sqliteCount(): number {
  const db = getDb();
  const row = db.prepare("SELECT COUNT(*) as cnt FROM request_access_leads").get() as { cnt: number };
  return row.cnt;
}

// ── Unified API (auto-detects backend) ──────────────────────────────────────

export async function createLead(data: {
  name: string;
  email: string;
  organization: string;
  roleType: string;
  organizationType: string;
  useCase: string;
  message?: string;
}): Promise<RequestAccessLead> {
  if (USE_PG) return pgCreate(data);
  return sqliteCreate(data);
}

export async function listLeads(limit = 200): Promise<RequestAccessLead[]> {
  if (USE_PG) return pgList(limit);
  return sqliteList(limit);
}

export async function countLeads(): Promise<number> {
  if (USE_PG) return pgCount();
  return sqliteCount();
}

function mapRow(row: Record<string, unknown>): RequestAccessLead {
  return {
    id: String(row.id),
    name: String(row.name),
    email: String(row.email),
    organization: String(row.organization),
    roleType: String(row.role_type),
    organizationType: String(row.organization_type),
    useCase: String(row.use_case),
    message: row.message ? String(row.message) : undefined,
    createdAt: String(row.created_at),
  };
}