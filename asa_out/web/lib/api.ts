import type {
  AccidentDetail,
  AccidentProvenance,
  AccidentSummary,
  AnalyticsSummary,
  ApiKeyRecord,
  ArchiveManifest,
  AuditLogItem,
  ClaimConflict,
  ConflictQueueItem,
  ConflictResolveRequest,
  CreatedApiKey,
  DataQualityIssue,
  DuplicateCandidate,
  MapAccident,
  IngestionRun,
  MapCluster,
  PaginatedAccidents,
  SearchFilters,
  Source,
  SourceDocument,
  SourceStatus,
} from '../types';

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

// ── Typed API errors ──────────────────────────────────────────────────────────
// The old implementation threw a generic Error for every non-2xx response,
// making it impossible for callers to distinguish 404 (gone), 409 (already
// resolved), and 422 (bad payload) — all of which require different UX.

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly path: string,
    public readonly detail?: unknown,
  ) {
    super(`API ${status}: ${path}`);
    this.name = 'ApiError';
  }
}

/** 404 — resource does not exist or has been removed */
export class NotFoundError extends ApiError {
  constructor(path: string, detail?: unknown) {
    super(404, path, detail);
    this.name = 'NotFoundError';
  }
}

/** 409 — state conflict (e.g. conflict already resolved by another reviewer) */
export class ConflictError extends ApiError {
  constructor(path: string, detail?: unknown) {
    super(409, path, detail);
    this.name = 'ConflictError';
  }
}

/** 422 — validation failure (bad request body or query params) */
export class ValidationError extends ApiError {
  constructor(path: string, detail?: unknown) {
    super(422, path, detail);
    this.name = 'ValidationError';
  }
}

/** Request exceeded the timeout threshold — safe to retry */
export class ApiTimeoutError extends Error {
  constructor(
    public readonly path: string,
    public readonly timeoutMs: number,
  ) {
    super(`Request timed out after ${timeoutMs}ms: ${path}`);
    this.name = 'ApiTimeoutError';
  }
}

function mapStatusToError(status: number, path: string, detail: unknown): ApiError {
  if (status === 404) return new NotFoundError(path, detail);
  if (status === 409) return new ConflictError(path, detail);
  if (status === 422) return new ValidationError(path, detail);
  return new ApiError(status, path, detail);
}

// ── Core fetch wrapper ────────────────────────────────────────────────────────

const DEFAULT_TIMEOUT_MS = 20_000;

function authHeaders(apiKey?: string, contentType = false): Record<string, string> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (contentType) headers['Content-Type'] = 'application/json';
  if (apiKey) headers['X-API-Key'] = apiKey;
  return headers;
}

interface FetchOptions extends RequestInit {
  timeoutMs?: number;
}

async function apiFetch<T>(
  path: string,
  params?: Record<string, string>,
  options: FetchOptions = {},
): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchInit } = options;

  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== '' && v !== undefined) url.searchParams.set(k, v);
    });
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(url.toString(), {
      headers: { Accept: 'application/json' },
      ...fetchInit,
      signal: controller.signal,
    });

    if (!res.ok) {
      let detail: unknown;
      try { detail = await res.json(); } catch { /* non-JSON body is fine */ }
      throw mapStatusToError(res.status, path, detail);
    }

    return res.json() as Promise<T>;
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiTimeoutError(path, timeoutMs);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// ── Map response shape ────────────────────────────────────────────────────────
// The backend now caps /accidents/map at MAX_MAP_RESULTS and returns a
// wrapper object instead of a bare array so callers can detect truncation.

export interface MapAccidentsResponse {
  mode?: 'points' | 'clusters';
  items: MapAccident[];
  clusters?: MapCluster[];
  count: number;
  truncated: boolean;
  limit: number;
  zoom?: number | null;
  cluster_cell_degrees?: number | null;
}

export async function fetchAccidents(
  filters: Partial<SearchFilters> & { page?: number; page_size?: number; cursor?: string | null }
): Promise<PaginatedAccidents> {
  const params: Record<string, string> = {};
  if (filters.q) params.q = filters.q;
  if (filters.severity) params.severity = filters.severity;
  if (filters.phase) params.phase = filters.phase;
  if (filters.year_from) params.year_from = filters.year_from;
  if (filters.min_source_completeness) params.min_source_completeness = filters.min_source_completeness;
  if (filters.fatality_status) params.fatality_status = filters.fatality_status;
  if (filters.registration) params.registration = filters.registration;
  if (filters.aircraft_type) params.aircraft_type = filters.aircraft_type;
  if (filters.operator) params.operator = filters.operator;
  if (filters.source_id) params.source_id = filters.source_id;
  if (filters.disputed_only) params.disputed_only = 'true';
  if (filters.final_report_only) params.final_report_only = 'true';
  if (filters.sort) params.sort = filters.sort;
  if (filters.cursor) params.cursor = filters.cursor;
  if (filters.page !== undefined && !filters.cursor) params.page = String(filters.page);
  if (filters.page_size !== undefined) params.page_size = String(filters.page_size);
  return apiFetch<PaginatedAccidents>('/api/v1/accidents', params);
}

export async function fetchAccident(id: string): Promise<AccidentDetail> {
  return apiFetch<AccidentDetail>(`/api/v1/accidents/${id}`);
}

export async function fetchProvenance(id: string): Promise<AccidentProvenance> {
  return apiFetch<AccidentProvenance>(`/api/v1/accidents/${id}/provenance`);
}

export async function fetchSources(): Promise<Source[]> {
  return apiFetch<Source[]>('/api/v1/sources');
}

export interface MapBounds {
  north: number;
  south: number;
  east: number;
  west: number;
}

export interface MapViewport extends MapBounds {
  zoom: number;
}

export async function fetchMapAccidents(filters?: {
  severity?: string;
  year_from?: string;
  year_to?: string;
  bounds?: MapBounds;
  zoom?: number;
}): Promise<MapAccidentsResponse> {
  if (filters?.bounds && filters.bounds.west > filters.bounds.east) {
    const left = await fetchMapAccidents({ ...filters, bounds: { ...filters.bounds, east: 180 } });
    const right = await fetchMapAccidents({ ...filters, bounds: { ...filters.bounds, west: -180 } });
    return {
      mode: left.mode === 'clusters' || right.mode === 'clusters' ? 'clusters' : 'points',
      items: [...left.items, ...right.items],
      clusters: [...(left.clusters ?? []), ...(right.clusters ?? [])],
      count: left.count + right.count,
      truncated: left.truncated || right.truncated,
      limit: left.limit + right.limit,
      zoom: filters.zoom ?? left.zoom ?? right.zoom ?? null,
      cluster_cell_degrees: left.cluster_cell_degrees ?? right.cluster_cell_degrees ?? null,
    };
  }
  const params: Record<string, string> = {};
  if (filters?.severity) params.severity = filters.severity;
  if (filters?.year_from) params.year_from = filters.year_from;
  if (filters?.year_to) params.year_to = filters.year_to;
  if (filters?.bounds) {
    params.north = String(filters.bounds.north);
    params.south = String(filters.bounds.south);
    params.east = String(filters.bounds.east);
    params.west = String(filters.bounds.west);
  }
  if (filters?.zoom !== undefined) params.zoom = String(filters.zoom);
  return apiFetch<MapAccidentsResponse>('/api/v1/accidents/map', params);
}

export async function fetchAnalyticsSummary(): Promise<AnalyticsSummary> {
  return apiFetch<AnalyticsSummary>('/api/v1/analytics/summary');
}

// ── Mock data for local dev when backend is unavailable ────────────────────
export const MOCK_ACCIDENTS: AccidentSummary[] = [
  {
    id: 'evt-001', canonical_id: 'NTSB-WPR23LA001',
    occurred_at: '2023-01-04T22:30:00', occurred_date: '2023-01-04', occurred_year: 2023,
    occurred_at_precision: 'exact',
    location_text: 'Bend, OR, USA',
    location_lat: 44.06, location_lon: -121.31, country_code: 'USA',
    aircraft_make: 'Cessna', aircraft_model: '172S',
    operator_name: 'Private', phase_of_flight: 'LANDING',
    injury_severity: 'SERIOUS', fatalities_total: 0, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 2,
    aircraft_damage: 'SUBSTANTIAL', investigation_status: 'final',
    confidence: { score: 0.91, label: 'Well sourced', css_class: 'conf-high', breakdown: null },
    has_conflicts: false, winning_source_count: 1, claim_source_count: 1, primary_source_id: 'src-ntsb-001',
  },
  {
    id: 'evt-002', canonical_id: 'NTSB-ERA23LA052',
    occurred_at: '2023-02-11T17:45:00', occurred_date: '2023-02-11', occurred_year: 2023,
    occurred_at_precision: 'exact',
    location_text: 'Fort Lauderdale, FL, USA',
    location_lat: 26.07, location_lon: -80.15, country_code: 'USA',
    aircraft_make: 'Piper', aircraft_model: 'PA-28-181',
    operator_name: 'Sun Country Aviation', phase_of_flight: 'TAKEOFF',
    injury_severity: 'FATAL', fatalities_total: 1, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 2,
    aircraft_damage: 'DESTROYED', investigation_status: 'final',
    confidence: { score: 0.88, label: 'Mostly sourced', css_class: "conf-good", breakdown: null },
    has_conflicts: false, winning_source_count: 1, claim_source_count: 1, primary_source_id: 'src-ntsb-001',
  },
  {
    id: 'evt-003', canonical_id: 'NTSB-CEN23LA089',
    occurred_at: '2023-03-05T20:12:00', occurred_date: '2023-03-05', occurred_year: 2023,
    occurred_at_precision: 'exact',
    location_text: 'Oklahoma City, OK, USA',
    location_lat: 35.46, location_lon: -97.51, country_code: 'USA',
    aircraft_make: 'Beechcraft', aircraft_model: 'Bonanza A36',
    operator_name: 'Private', phase_of_flight: 'CRUISE',
    injury_severity: 'FATAL', fatalities_total: 3, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 3,
    aircraft_damage: 'DESTROYED', investigation_status: 'final',
    confidence: { score: 0.94, label: 'Well sourced', css_class: 'conf-high', breakdown: null },
    has_conflicts: false, winning_source_count: 1, claim_source_count: 1, primary_source_id: 'src-ntsb-001',
  },
  {
    id: 'evt-004', canonical_id: 'NTSB-WPR23FA103',
    occurred_at: '2023-04-17T19:00:00', occurred_date: '2023-04-17', occurred_year: 2023,
    occurred_at_precision: 'exact',
    location_text: 'Scottsdale, AZ, USA',
    location_lat: 33.62, location_lon: -111.91, country_code: 'USA',
    aircraft_make: 'Robinson', aircraft_model: 'R44 II',
    operator_name: 'Desert Helicopters LLC', phase_of_flight: 'MANEUVERING',
    injury_severity: 'FATAL', fatalities_total: 2, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 2,
    aircraft_damage: 'DESTROYED', investigation_status: 'preliminary',
    confidence: { score: 0.79, label: 'Mostly sourced', css_class: 'conf-good', breakdown: null },
    has_conflicts: false, winning_source_count: 1, claim_source_count: 1, primary_source_id: 'src-ntsb-001',
  },
  {
    id: 'evt-005', canonical_id: 'NTSB-ERA23FA128',
    occurred_at: '2023-05-09T22:00:00', occurred_date: '2023-05-09', occurred_year: 2023,
    occurred_at_precision: 'exact',
    location_text: 'Nashville, TN, USA',
    location_lat: 36.17, location_lon: -86.77, country_code: 'USA',
    aircraft_make: 'Cirrus', aircraft_model: 'SR22T',
    operator_name: 'Private', phase_of_flight: 'APPROACH',
    injury_severity: 'FATAL', fatalities_total: 4, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 4,
    aircraft_damage: 'DESTROYED', investigation_status: 'preliminary',
    confidence: { score: 0.62, label: 'Partially sourced', css_class: 'conf-partial', breakdown: null },
    has_conflicts: false, winning_source_count: 1, claim_source_count: 1, primary_source_id: 'src-ntsb-001',
  },
  {
    id: 'evt-006', canonical_id: 'NTSB-ANC23LA201',
    occurred_at: '2023-06-22T21:30:00', occurred_date: '2023-06-22', occurred_year: 2023,
    occurred_at_precision: 'exact',
    location_text: 'Anchorage, AK, USA',
    location_lat: 61.21, location_lon: -149.9, country_code: 'USA',
    aircraft_make: 'Piper', aircraft_model: 'PA-18-150 Super Cub',
    operator_name: 'Bush Air LLC', phase_of_flight: 'TAKEOFF',
    injury_severity: 'MINOR', fatalities_total: 0, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 1,
    aircraft_damage: 'SUBSTANTIAL', investigation_status: 'final',
    confidence: { score: 0.96, label: 'Well sourced', css_class: 'conf-high', breakdown: null },
    has_conflicts: false, winning_source_count: 1, claim_source_count: 1, primary_source_id: 'src-ntsb-001',
  },
  {
    id: 'evt-007', canonical_id: 'NTSB-CEN22LA301',
    occurred_at: '2022-08-14T19:45:00', occurred_date: '2022-08-14', occurred_year: 2022,
    occurred_at_precision: 'exact',
    location_text: 'Dallas, TX, USA',
    location_lat: 32.89, location_lon: -97.04, country_code: 'USA',
    aircraft_make: 'Boeing', aircraft_model: '737-800',
    operator_name: 'Southwest Airlines', phase_of_flight: 'LANDING',
    injury_severity: 'NONE', fatalities_total: 0, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 148,
    aircraft_damage: 'MINOR', investigation_status: 'final',
    confidence: { score: 0.97, label: 'Well sourced', css_class: 'conf-high', breakdown: null },
    has_conflicts: false, winning_source_count: 1, claim_source_count: 1, primary_source_id: 'src-ntsb-001',
  },
  {
    id: 'evt-008', canonical_id: 'NTSB-WPR22FA087',
    occurred_at: '2022-05-19T20:00:00', occurred_date: '2022-05-19', occurred_year: 2022,
    occurred_at_precision: 'exact',
    location_text: 'Sacramento, CA, USA',
    location_lat: 38.51, location_lon: -121.49, country_code: 'USA',
    aircraft_make: 'Cessna', aircraft_model: '210N',
    operator_name: 'Private', phase_of_flight: 'CLIMB',
    injury_severity: 'FATAL', fatalities_total: 2, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 2,
    aircraft_damage: 'DESTROYED', investigation_status: 'final',
    confidence: { score: 0.85, label: 'Mostly sourced', css_class: 'conf-good', breakdown: null },
    has_conflicts: false, winning_source_count: 1, claim_source_count: 1, primary_source_id: 'src-ntsb-001',
  },
  {
    id: 'evt-009', canonical_id: 'NTSB-ERA22LA119',
    occurred_at: '2022-03-07T18:30:00', occurred_date: '2022-03-07', occurred_year: 2022,
    occurred_at_precision: 'exact',
    location_text: 'Atlanta, GA, USA',
    location_lat: 33.75, location_lon: -84.39, country_code: 'USA',
    aircraft_make: 'Diamond', aircraft_model: 'DA40',
    operator_name: 'Aviator Flight Academy', phase_of_flight: 'LANDING',
    injury_severity: 'MINOR', fatalities_total: 0, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 2,
    aircraft_damage: 'SUBSTANTIAL', investigation_status: 'final',
    confidence: { score: 0.88, label: 'Mostly sourced', css_class: "conf-good", breakdown: null },
    has_conflicts: false, winning_source_count: 1, claim_source_count: 1, primary_source_id: 'src-ntsb-001',
  },
  {
    id: 'evt-010', canonical_id: 'NTSB-CEN21FA049',
    occurred_at: '2021-11-03T06:00:00', occurred_date: '2021-11-03', occurred_year: 2021,
    occurred_at_precision: 'exact',
    location_text: 'Denver, CO, USA',
    location_lat: 39.86, location_lon: -104.67, country_code: 'USA',
    aircraft_make: 'Piper', aircraft_model: 'PA-34-220T Seneca',
    operator_name: 'Mountain Air Charter', phase_of_flight: 'DESCENT',
    injury_severity: 'FATAL', fatalities_total: 5, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 5,
    aircraft_damage: 'DESTROYED', investigation_status: 'final',
    confidence: { score: 0.90, label: 'Well sourced', css_class: 'conf-high', breakdown: null },
    has_conflicts: false, winning_source_count: 1, claim_source_count: 1, primary_source_id: 'src-ntsb-001',
  },
  {
    id: 'evt-011', canonical_id: 'NTSB-ERA21FA311',
    occurred_at: '2021-09-28T14:00:00', occurred_date: '2021-09-28', occurred_year: 2021,
    occurred_at_precision: 'exact',
    location_text: 'Miami, FL, USA',
    location_lat: 25.79, location_lon: -80.28, country_code: 'USA',
    aircraft_make: 'Cessna', aircraft_model: '560XL Citation',
    operator_name: 'Excel Air LLC', phase_of_flight: 'APPROACH',
    injury_severity: 'FATAL', fatalities_total: 2, fatalities_crew: null, fatalities_passengers: null,
    serious_injuries_crew: null, serious_injuries_passengers: null,
    minor_injuries_crew: null, minor_injuries_passengers: null,
    uninjured_crew: null, uninjured_passengers: null,
    aboard_total: 5,
    aircraft_damage: 'DESTROYED', investigation_status: 'final',
    confidence: { score: 0.75, label: 'Mostly sourced', css_class: 'conf-good', breakdown: null },
    has_conflicts: true, winning_source_count: 1, claim_source_count: 2, primary_source_id: 'src-ntsb-001',
  },
];

export const MOCK_DETAIL: Record<string, AccidentDetail> = {
  'evt-001': {
    ...MOCK_ACCIDENTS[0],
    // The mock provenance has an open dispute over fatalities_total (NTSB=0,
    // ASN=1).  In v20 the projection withholds disputed fields, so the
    // detail record should NOT carry a confident projected value here —
    // that's the whole point of the safety fix.
    fatalities_total: null,
    fatalities_crew: null,
    fatalities_passengers: null,
    injury_severity: null,
    probable_cause: "The pilot's failure to maintain adequate airspeed during the approach, which resulted in an aerodynamic stall and hard landing.",
    contributing_factors: ['Low airspeed on final approach', 'Gusty crosswind conditions'],
    ntsb_report_number: 'WPR23LA001',
    weather_condition: 'VMC',
    purpose_of_flight: 'Personal',
    aircraft_registration: 'N12345',
    aircraft_amateur_built: false,
    serious_injuries: 1,
    minor_injuries: 0,
    state_code: 'OR',
    last_projected_at: '2024-01-15T10:00:00Z',
    // Document is linked but not yet URL-verified — drives the
    // "Linked, unverified" document status in the evidence bar.
    document_status: 'linked_unverified',
  },
  'evt-002': {
    ...MOCK_ACCIDENTS[1],
    probable_cause: "Loss of engine power due to fuel starvation caused by the pilot's failure to switch fuel tanks during extended flight.",
    contributing_factors: ['Fuel mismanagement', 'Pilot failure to monitor fuel gauges'],
    ntsb_report_number: 'ERA23LA052',
    weather_condition: 'VMC',
    purpose_of_flight: 'Instructional',
    aircraft_registration: 'N67890',
    aircraft_amateur_built: false,
    serious_injuries: 0,
    minor_injuries: 1,
    state_code: 'FL',
    last_projected_at: '2024-01-15T10:00:00Z',
    // No SourceDocuments wired for this record — match real-world v19/v20
    // behaviour where NTSB ingestion does not yet create SourceDocument
    // rows.
    document_status: 'none_linked',
  },
};

export const MOCK_PROVENANCE: AccidentProvenance = {
  event_id: 'evt-001',
  // Mock claims demonstrate two scenarios:
  //   1. fields with a single confirmed source → winning claim, "Confirmed"
  //   2. fatalities_total has TWO conflicting source claims (NTSB=0 and
  //      ASN=1).  Both are 'disputed', NEITHER is winning — the v20 backend
  //      must withhold a projected value while a dispute is open.  The
  //      frontend must show "No projected value while this dispute is
  //      open." for this case and must NOT fabricate a winner.
  claims: [
    { id: 'c-001', field_name: 'occurred_at', field_value: { v: '2023-01-04T22:30:00', type: 'datetime' }, display_value: '2023-01-04 22:30 (local time, tz unknown)', claim_type: 'confirmed', confidence: 0.99, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: true, notes: null },
    { id: 'c-002', field_name: 'location_text', field_value: { v: 'Bend, OR, USA', type: 'str' }, display_value: 'Bend, OR, USA', claim_type: 'confirmed', confidence: 0.95, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: true, notes: null },
    { id: 'c-003', field_name: 'injury_severity', field_value: { v: 'SERIOUS', type: 'str' }, display_value: 'SERIOUS', claim_type: 'confirmed', confidence: 0.99, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: true, notes: null },
    // ── disputed pair: NTSB says 0, ASN says 1 ────────────────────────
    // Both DISPUTED, neither is_winning — projection withholds.
    { id: 'c-fatalities-ntsb', field_name: 'fatalities_total', field_value: { v: 0, type: 'int' }, display_value: '0', claim_type: 'disputed', confidence: 0.99, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: false, notes: null },
    { id: 'c-fatalities-asn', field_name: 'fatalities_total', field_value: { v: 1, type: 'int' }, display_value: '1', claim_type: 'disputed', confidence: 0.85, source_id: 'src-asn-001', source_short_name: 'ASN', snapshot_id: 'snap-asn-001', effective_at: null, is_winning: false, notes: null },
    { id: 'c-fatalities-old', field_name: 'fatalities_total', field_value: { v: 2, type: 'int' }, display_value: '2', claim_type: 'rejected', confidence: 0.20, source_id: 'src-asn-001', source_short_name: 'ASN', snapshot_id: 'snap-asn-old', effective_at: null, is_winning: false, notes: 'Rejected during conflict review.' },
    { id: 'c-005', field_name: 'aircraft_make', field_value: { v: 'Cessna', type: 'str' }, display_value: 'Cessna', claim_type: 'confirmed', confidence: 0.99, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: true, notes: null },
    { id: 'c-006', field_name: 'aircraft_model', field_value: { v: '172S', type: 'str' }, display_value: '172S', claim_type: 'confirmed', confidence: 0.99, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: true, notes: null },
    { id: 'c-007', field_name: 'phase_of_flight', field_value: { v: 'LANDING', type: 'str' }, display_value: 'LANDING', claim_type: 'confirmed', confidence: 0.95, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: true, notes: null },
    { id: 'c-008', field_name: 'aircraft_damage', field_value: { v: 'SUBSTANTIAL', type: 'str' }, display_value: 'SUBSTANTIAL', claim_type: 'confirmed', confidence: 0.99, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: true, notes: null },
    { id: 'c-009', field_name: 'investigation_status', field_value: { v: 'final', type: 'str' }, display_value: 'final', claim_type: 'confirmed', confidence: 0.99, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: true, notes: null },
    { id: 'c-010', field_name: 'operator_name', field_value: { v: 'PRIVATE', type: 'str' }, display_value: 'PRIVATE', claim_type: 'confirmed', confidence: 0.90, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: true, notes: null },
    { id: 'c-011', field_name: 'probable_cause', field_value: { v: "The pilot's failure to maintain adequate airspeed during the approach, which resulted in an aerodynamic stall and hard landing.", type: 'str' }, display_value: "The pilot's failure to maintain adequate airspeed during the approach, which resulted in an aerodynamic stall and hard landing.", claim_type: 'confirmed', confidence: 0.95, source_id: 'src-ntsb-001', source_short_name: 'NTSB', snapshot_id: 'snap-001', effective_at: null, is_winning: true, notes: null },
  ],
  conflicts: [
    // The conflict references real claim ids (above).  All ClaimConflict
    // fields are populated — including the structured resolution fields
    // that pre-v20 mocks had been omitting, which caused TS type drift.
    {
      id: 'conflict-fatalities',
      field_name: 'fatalities_total',
      claim_a_id: 'c-fatalities-ntsb',
      claim_b_id: 'c-fatalities-asn',
      status: 'open',
      resolution: null,
      resolution_type: null,
      accepted_claim_id: null,
      rejected_claim_ids: null,
      resolved_at: null,
      resolved_by: null,
      obsolete_reason: null,
    },
  ],
  source_documents: [
    // Linked but not yet URL-verified — drives the "Linked, unverified"
    // document status in the evidence bar.  Includes the v19 verification-
    // metadata fields (last_http_status / last_check_error / last_check_method)
    // that the SourceDocument type requires.
    {
      id: 'doc-001',
      event_id: 'evt-001',
      source_id: 'src-ntsb-001',
      document_type: 'docket',
      url: 'https://www.ntsb.gov/investigations/AccidentReports/Pages/WPR23LA001.aspx',
      url_verified: false,
      title: 'NTSB Investigation: WPR23LA001',
      published_at: '2023-06-01',
      is_available: null,
      last_checked_at: null,
      last_http_status: null,
      last_check_error: null,
      last_check_method: null,
    },
  ],
  sources: [
    { id: 'src-ntsb-001', short_name: 'NTSB', display_name: 'National Transportation Safety Board', tier: 1, license_type: 'public_domain', base_url: 'https://www.ntsb.gov', description: 'Primary US aviation accident authority. Public domain.' },
    { id: 'src-asn-001', short_name: 'ASN', display_name: 'Aviation Safety Network', tier: 2, license_type: 'licensed', base_url: 'https://aviation-safety.net', description: 'Global records since 1919. Secondary source for cross-checking NTSB.' },
  ],
  // No projection explanation for fatalities_total — the dispute is open
  // and there is no winning claim, so the projection has nothing to
  // explain. Other fields with single-source winning claims are omitted
  // here for brevity; the UI works with a partial projections array.
  projections: [
    {
      field_name: 'fatalities_total',
      displayed_value: null,
      selected_claim_id: null,
      selected_source_id: null,
      source_rank: null,
      selection_reason: 'withheld_open_dispute',
      has_open_conflict: true,
      supporting_claim_count: 0,
      disputed_claim_count: 2,
    },
  ],
  // No backend revisions in mock mode — the frontend falls back to its
  // derived timeline (published documents, resolved conflicts, record
  // rebuilt). When the backend supplies real revisions, the UI will use
  // those instead.
  revisions: [],
};

// ── Conflict resolution ────────────────────────────────────────────────────

/**
 * Resolve an open claim conflict.
 *
 * Pass apiKey when API_AUTH_ENABLED=true on the backend.
 * When auth is disabled, resolved_by in the body identifies the operator.
 */
export async function resolveConflict(
  conflictId: string,
  body: ConflictResolveRequest,
  apiKey?: string,
): Promise<ClaimConflict> {
  const path = `/api/v1/conflicts/${encodeURIComponent(conflictId)}/resolve`;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  };
  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  }
  // Use apiFetch so this call gets timeout protection and typed error mapping.
  // 404 → NotFoundError (conflict gone / stale link)
  // 409 → ConflictError (already resolved by another reviewer)
  // 422 → ValidationError (invalid resolution payload)
  return apiFetch<ClaimConflict>(path, undefined, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
}

// ── Conflict queue endpoints ───────────────────────────────────────────────────
// These were previously inline bare-fetch functions in conflicts.tsx.
// Moving them here gives them timeout protection, typed error classes, and a
// single place to update if the endpoint paths change.

export interface ConflictStats {
  by_status: Record<string, number>;
  top_disputed_fields: { field: string; open_count: number }[];
}

export async function fetchConflictQueue(
  fieldName?: string,
  limit = 100,
  apiKey?: string,
): Promise<ConflictQueueItem[]> {
  const params: Record<string, string> = { limit: String(limit) };
  if (fieldName) params.field_name = fieldName;
  return apiFetch<ConflictQueueItem[]>('/api/v1/conflicts', params, { headers: authHeaders(apiKey) });
}

export async function fetchConflictStats(apiKey?: string): Promise<ConflictStats> {
  return apiFetch<ConflictStats>('/api/v1/conflicts/stats', undefined, { headers: authHeaders(apiKey) });
}


export async function confirmDuplicateCandidate(
  candidateId: string,
  apiKey?: string,
  note?: string,
): Promise<DuplicateCandidate> {
  const headers = authHeaders(apiKey, true);
  return apiFetch<DuplicateCandidate>(`/api/v1/duplicates/${encodeURIComponent(candidateId)}/confirm`, undefined, {
    method: 'POST',
    headers,
    body: JSON.stringify({ note: note ?? null }),
  });
}


export async function undoDuplicateCandidate(
  candidateId: string,
  apiKey?: string,
  note?: string,
): Promise<DuplicateCandidate> {
  return apiFetch<DuplicateCandidate>(`/api/v1/duplicates/${encodeURIComponent(candidateId)}/undo`, undefined, {
    method: 'POST',
    headers: authHeaders(apiKey, true),
    body: JSON.stringify({ note: note ?? null }),
  });
}

export async function rejectDuplicateCandidate(
  candidateId: string,
  apiKey?: string,
  note?: string,
): Promise<DuplicateCandidate> {
  const headers = authHeaders(apiKey, true);
  return apiFetch<DuplicateCandidate>(`/api/v1/duplicates/${encodeURIComponent(candidateId)}/reject`, undefined, {
    method: 'POST',
    headers,
    body: JSON.stringify({ note: note ?? null }),
  });
}

export async function resolveDataQualityIssue(
  issueId: string,
  apiKey?: string,
  note?: string,
): Promise<DataQualityIssue> {
  const headers = authHeaders(apiKey, true);
  return apiFetch<DataQualityIssue>(`/api/v1/data-quality/issues/${encodeURIComponent(issueId)}/resolve`, undefined, {
    method: 'POST',
    headers,
    body: JSON.stringify({ note: note ?? null }),
  });
}


export async function fetchDuplicateCandidatesWithAuth(
  apiKey: string | undefined,
  status = 'pending',
  limit = 100,
): Promise<DuplicateCandidate[]> {
  return apiFetch<DuplicateCandidate[]>('/api/v1/duplicates', { status, limit: String(limit) }, { headers: authHeaders(apiKey) });
}

export async function fetchDataQualityIssuesWithAuth(
  apiKey: string | undefined,
  status = 'open',
  issueCode?: string,
  limit = 100,
): Promise<DataQualityIssue[]> {
  const params: Record<string, string> = { status, limit: String(limit) };
  if (issueCode) params.issue_code = issueCode;
  return apiFetch<DataQualityIssue[]>('/api/v1/data-quality/issues', params, { headers: authHeaders(apiKey) });
}

export async function fetchSourceStatus(apiKey?: string): Promise<SourceStatus[]> {
  return apiFetch<SourceStatus[]>('/api/v1/ops/source-status', undefined, { headers: authHeaders(apiKey) });
}

export async function fetchIngestionRuns(apiKey?: string, limit = 100): Promise<IngestionRun[]> {
  return apiFetch<IngestionRun[]>('/api/v1/ops/ingestion-runs', { limit: String(limit) }, { headers: authHeaders(apiKey) });
}

export async function fetchArchiveManifests(apiKey?: string, limit = 100): Promise<ArchiveManifest[]> {
  return apiFetch<ArchiveManifest[]>('/api/v1/admin/archive/manifests', { limit: String(limit) }, { headers: authHeaders(apiKey) });
}

export async function verifyArchiveManifest(manifestId: string, apiKey?: string): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(`/api/v1/admin/archive/manifests/${encodeURIComponent(manifestId)}/verify`, undefined, { headers: authHeaders(apiKey) });
}

export async function fetchAuditLog(apiKey?: string, limit = 100): Promise<AuditLogItem[]> {
  return apiFetch<AuditLogItem[]>('/api/v1/admin/audit-log', { limit: String(limit) }, { headers: authHeaders(apiKey) });
}

export async function fetchApiKeys(apiKey?: string): Promise<ApiKeyRecord[]> {
  return apiFetch<ApiKeyRecord[]>('/api/v1/admin/api-keys', undefined, { headers: authHeaders(apiKey) });
}

export async function createApiKey(
  apiKey: string | undefined,
  body: { operator_id: string; role: 'reviewer' | 'admin'; description?: string | null },
): Promise<CreatedApiKey> {
  return apiFetch<CreatedApiKey>('/api/v1/admin/api-keys', undefined, {
    method: 'POST',
    headers: authHeaders(apiKey, true),
    body: JSON.stringify(body),
  });
}

export async function revokeApiKey(keyId: string, apiKey?: string): Promise<ApiKeyRecord> {
  return apiFetch<ApiKeyRecord>(`/api/v1/admin/api-keys/${encodeURIComponent(keyId)}/revoke`, undefined, {
    method: 'POST',
    headers: authHeaders(apiKey, true),
  });
}

export async function fetchSourceDocuments(apiKey?: string, limit = 100): Promise<SourceDocument[]> {
  return apiFetch<SourceDocument[]>('/api/v1/admin/source-documents', { limit: String(limit) }, { headers: authHeaders(apiKey) });
}

export async function reviewSourceDocument(
  documentId: string,
  apiKey: string | undefined,
  body: { document_type?: string | null; url_verified?: boolean | null; is_available?: boolean | null; note?: string | null },
): Promise<SourceDocument> {
  return apiFetch<SourceDocument>(`/api/v1/admin/source-documents/${encodeURIComponent(documentId)}/review`, undefined, {
    method: 'POST',
    headers: authHeaders(apiKey, true),
    body: JSON.stringify(body),
  });
}

// ── Timeline API ──────────────────────────────────────────────────────────────

import type { AccidentTimeline } from '../types';

export async function fetchAccidentTimeline(eventId: string): Promise<AccidentTimeline> {
  return apiFetch<AccidentTimeline>(`/api/v1/accidents/${encodeURIComponent(eventId)}/timeline`);
}

export async function rebuildAccidentTimeline(
  eventId: string,
  apiKey?: string,
): Promise<AccidentTimeline> {
  return apiFetch<AccidentTimeline>(
    `/api/v1/accidents/${encodeURIComponent(eventId)}/timeline/rebuild`,
    undefined,
    { method: 'POST', headers: authHeaders(apiKey) },
  );
}

// ── Weather API ───────────────────────────────────────────────────────────────

import type { WeatherContext } from '../types';

export async function fetchAccidentWeather(eventId: string): Promise<WeatherContext> {
  return apiFetch<WeatherContext>(`/api/v1/accidents/${encodeURIComponent(eventId)}/weather`);
}

export async function rebuildAccidentWeather(
  eventId: string,
  apiKey?: string,
): Promise<WeatherContext> {
  return apiFetch<WeatherContext>(
    `/api/v1/accidents/${encodeURIComponent(eventId)}/weather/rebuild`,
    undefined,
    { method: 'POST', headers: authHeaders(apiKey) },
  );
}

// ── System Failures API ───────────────────────────────────────────────────────

import type { SystemFailures } from '../types';

export async function fetchSystemFailures(eventId: string): Promise<SystemFailures> {
  return apiFetch<SystemFailures>(`/api/v1/accidents/${encodeURIComponent(eventId)}/system-failures`);
}

// ── Advanced Analytics API ────────────────────────────────────────────────────

import type {
  AdvancedSummary, SimilarAccidentsResult,
  DataQualitySummary, SystemFailurePatterns,
} from '../types';

export async function fetchAdvancedSummary(): Promise<AdvancedSummary> {
  return apiFetch<AdvancedSummary>('/api/v1/analytics/advanced/summary');
}

export async function fetchSimilarAccidents(
  eventId: string,
  limit = 8,
): Promise<SimilarAccidentsResult> {
  return apiFetch<SimilarAccidentsResult>(
    `/api/v1/accidents/${encodeURIComponent(eventId)}/similar`,
    { limit: String(limit) },
  );
}

export async function fetchDataQuality(): Promise<DataQualitySummary> {
  return apiFetch<DataQualitySummary>('/api/v1/analytics/advanced/data-quality');
}

export async function fetchSystemFailurePatterns(): Promise<SystemFailurePatterns> {
  return apiFetch<SystemFailurePatterns>('/api/v1/analytics/advanced/system-failures');
}

// ── Flight Path API ───────────────────────────────────────────────────────────

import type { FlightPathReconstruction, FlightPathProfile } from '../types';

export async function fetchFlightPath(eventId: string): Promise<FlightPathReconstruction> {
  return apiFetch<FlightPathReconstruction>(
    `/api/v1/accidents/${encodeURIComponent(eventId)}/flight-path`
  );
}

export async function fetchFlightPathProfile(eventId: string): Promise<FlightPathProfile> {
  return apiFetch<FlightPathProfile>(
    `/api/v1/accidents/${encodeURIComponent(eventId)}/flight-path/profile`
  );
}
