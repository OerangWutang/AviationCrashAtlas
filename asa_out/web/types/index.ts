export type ConfidenceLabel =
  | 'Well sourced'
  | 'Mostly sourced'
  | 'Partially sourced'
  | 'Weakly sourced';

export interface Confidence {
  score: number;
  label: ConfidenceLabel;
  css_class: string;
  breakdown: ConfidenceBreakdown | null;
}

export interface ConfidenceFactor {
  name: string;
  delta: number;
  reason: string;
}

export interface ConfidenceBreakdown {
  event_id: string;
  base_score: number;
  final_score: number;
  label: string;
  claim_count: number;
  /** Actionable disputes — these block projection and penalize source completeness. */
  open_conflict_count: number;
  /** Manually settled disputes — no longer actionable. */
  resolved_conflict_count: number;
  /** Disputes involving superseded claims — no longer actionable. */
  obsolete_conflict_count: number;
  /** Total conflicts (all statuses). Use open_conflict_count for actionable disputes. */
  conflict_count: number;
  source_tiers: number[];
  factors: ConfidenceFactor[];
}

/**
 * Typed claim value envelope. Matches the backend claim_value.encode() output:
 *   { "v": <json-safe value>, "type": "<type tag>" }
 *
 * Use claimValueDisplay() in utils.ts to render any variant as a string.
 */
export type ClaimValue =
  | { v: null; type: 'null' }
  | { v: string; type: 'str' | 'datetime' | 'date' }
  | { v: number; type: 'int' | 'float' }
  | { v: boolean; type: 'bool' }
  | { v: ClaimValue[]; type: 'list' }
  | { v: Record<string, ClaimValue>; type: 'dict' };

export interface AccidentSummary {
  id: string;
  canonical_id: string;
  occurred_at: string | null;
  occurred_date: string | null;
  occurred_year: number | null;
  /** "exact" | "day" | "year" — how precise the time value is. Never display
   *  a day-precision or year-precision value as if it were an exact timestamp. */
  occurred_at_precision: string | null;
  location_text: string | null;
  location_lat: number | null;
  location_lon: number | null;
  country_code: string | null;
  aircraft_make: string | null;
  aircraft_model: string | null;
  operator_name: string | null;
  phase_of_flight: string | null;
  injury_severity: 'FATAL' | 'SERIOUS' | 'MINOR' | 'NONE' | 'UNKNOWN' | null;
  fatalities_total: number | null;
  fatalities_crew: number | null;
  fatalities_passengers: number | null;
  serious_injuries_crew: number | null;
  serious_injuries_passengers: number | null;
  minor_injuries_crew: number | null;
  minor_injuries_passengers: number | null;
  uninjured_crew: number | null;
  uninjured_passengers: number | null;
  aboard_total: number | null;
  aircraft_damage: string | null;
  investigation_status: string | null;
  confidence: Confidence;
  has_conflicts: boolean;
  /** Sources behind projected (winning) field values only. */
  winning_source_count: number;
  /** All sources that contributed any non-superseded claim, including non-winning. */
  claim_source_count: number;
  primary_source_id: string | null;
}

export interface AccidentDetail extends AccidentSummary {
  probable_cause: string | null;
  contributing_factors: string[] | null;
  ntsb_report_number: string | null;
  weather_condition: string | null;
  purpose_of_flight: string | null;
  aircraft_registration: string | null;
  aircraft_amateur_built: boolean | null;
  serious_injuries: number | null;
  minor_injuries: number | null;
  state_code: string | null;
  /**
   * When the read projection (accident_records row) was last rebuilt
   * from the claim store. This is a LOCAL bookkeeping timestamp — it
   * does NOT mean the source data changed, and the UI must not display
   * it as "Last updated".
   */
  last_projected_at: string;
  /**
   * Aggregate document-status label, computed by the backend from the
   * actual SourceDocument rows for this event.  One of:
   *   'none_linked'        — zero documents
   *   'linked_unverified'  — documents exist, none verified
   *   'verified'           — at least one verified available document
   *   'unavailable'        — documents exist but all unavailable
   *   'mixed'              — heterogeneous states across documents
   *
   * Nullable so older API responses (pre-v20) still type-check; the UI
   * treats null as 'none_linked'.
   */
  document_status: DocumentStatus | null;
}

export type DocumentStatus =
  | 'none_linked'
  | 'linked_unverified'
  | 'verified'
  | 'unavailable'
  | 'mixed';

export const CLAIM_TYPES = [
  'confirmed',
  'inferred',
  'disputed',
  'rejected',
  'superseded',
  'pending',
] as const;

export type ClaimType = (typeof CLAIM_TYPES)[number];

export function isClaimType(value: unknown): value is ClaimType {
  return typeof value === 'string' && (CLAIM_TYPES as readonly string[]).includes(value);
}

export interface Claim {
  id: string;
  field_name: string;
  field_value: ClaimValue;
  display_value: string;
  claim_type: ClaimType;
  confidence: number | null;
  source_id: string;
  source_short_name: string | null;
  snapshot_id: string | null;
  effective_at: string | null;
  is_winning: boolean;
  notes: string | null;
}

export interface ClaimConflict {
  id: string;
  field_name: string;
  claim_a_id: string;
  claim_b_id: string;
  /** Lifecycle state: 'open' | 'resolved' | 'obsolete' */
  status: 'open' | 'resolved' | 'obsolete';
  resolution: string | null;
  resolved_at: string | null;
  /** Structured resolution type: claim_accepted | claim_rejected | claims_merged | ... */
  resolution_type: string | null;
  /** The claim whose value was accepted as authoritative */
  accepted_claim_id: string | null;
  /** Claims that were explicitly rejected */
  rejected_claim_ids: string[] | null;
  obsolete_reason: string | null;
  /** Operator who resolved (from auth context or body.resolved_by) */
  resolved_by: string | null;
}

/**
 * Request body for POST /api/v1/conflicts/{id}/resolve.
 * resolved_by is used when API_AUTH_ENABLED=false; otherwise the server
 * derives it from the authenticated API key.
 */
export interface ConflictResolveRequest {
  resolution_type:
    | 'claim_accepted'
    | 'claim_rejected'
    | 'claims_merged'
    | 'source_corrected'
    | 'not_applicable'
    | 'manual_override';
  accepted_claim_id?: string | null;
  rejected_claim_ids?: string[] | null;
  resolution?: string | null;
  resolved_by?: string | null;
}

/**
 * Backend-supplied explanation for a single projected field value.
 *
 * Generated by ProjectionService.  The frontend MUST NOT invent these —
 * if `selection_reason` is null, the UI must say "no projected value"
 * rather than fabricate a justification.
 */
export interface ProjectionExplanation {
  field_name: string;
  /** Decoded display string of the displayed value, or null if withheld. */
  displayed_value: string | null;
  /** Claim id selected as the displayed value, or null if withheld. */
  selected_claim_id: string | null;
  selected_source_id: string | null;
  /** Tier of the selected source; useful for tier-vs-tier display. */
  source_rank: number | null;
  /**
   * Backend-generated machine code for the selection rationale.
   * Documented values:
   *   only_active_claim
   *   selected_official_final
   *   selected_latest_official
   *   selected_higher_tier
   *   withheld_open_dispute
   *   withheld_no_active_claim
   *   approximate_nearest_city_only
   */
  selection_reason: string | null;
  /** Whether at least one open conflict references this field. */
  has_open_conflict: boolean;
  supporting_claim_count: number;
  disputed_claim_count: number;
}

/**
 * One row of the human-readable accident-event timeline.
 *
 * Distinct from claim-history audit rows — event_revisions is what the
 * "How this record evolved" UI strip renders.  Generated by the
 * ingestion pipeline (snapshot first seen, snapshot changed, claim
 * superseded, conflict opened, document linked, …) so the UI does not
 * have to synthesize fake timeline events.
 */
export interface EventRevision {
  id: string;
  event_id: string;
  /** Documented values listed in migration 0008. */
  revision_type: string;
  occurred_at: string;
  source_id: string | null;
  source_short_name: string | null;
  field_names: string[] | null;
  description: string | null;
}

export interface SourceDocument {
  id: string;
  event_id: string;
  source_id: string;
  document_type: string;
  url: string;
  url_verified: boolean;
  title: string | null;
  published_at: string | null;
  is_available: boolean | null;
  last_checked_at: string | null;
  /** HTTP status code from most recent check-links run */
  last_http_status: number | null;
  /** Error message or failure reason from most recent check */
  last_check_error: string | null;
  /** HTTP method used: "HEAD" or "GET" (HEAD→GET fallback) */
  last_check_method: string | null;
}

export interface Source {
  id: string;
  short_name: string;
  display_name: string;
  tier: number;
  license_type: string;
  base_url: string | null;
  description: string | null;
}

/**
 * Signals which provenance sub-sections were capped before being returned.
 * When any boolean field is true the UI should warn the user they are
 * seeing a subset and suggest narrowing the event or contacting support.
 */
export interface ProvenanceTruncation {
  claims: boolean;
  conflicts: boolean;
  source_documents: boolean;
  claims_limit: number;
  conflicts_limit: number;
  source_documents_limit: number;
}

export interface DataQualityIssue {
  id: string;
  event_id: string;
  source_id: string | null;
  issue_code: string;
  field_name: string;
  severity: string;
  status: string;
  details: Record<string, unknown> | null;
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution_note: string | null;
}

export interface DuplicateCandidate {
  id: string;
  source_event_id: string | null;
  candidate_event_id: string;
  source_id: string | null;
  source_record_id: string | null;
  match_type: string;
  match_score: number;
  match_reasons: string[] | null;
  status: string;
  decision_note: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface AccidentProvenance {
  event_id: string;
  claims: Claim[];
  conflicts: ClaimConflict[];
  source_documents: SourceDocument[];
  sources: Source[];
  /**
   * Backend-computed projection rationale, one entry per projected
   * field.  Optional so older API responses still type-check; the UI
   * treats absent/empty as "no explanations available" rather than
   * inventing reasons.
   */
  projections?: ProjectionExplanation[];
  /**
   * Real timeline of accident-event changes (snapshot first seen,
   * snapshot changed, claim superseded, conflict opened/resolved,
   * document linked/unavailable, projection rebuilt).  Optional for
   * the same forward-compat reason.  When absent, the UI shows only
   * the "Record rebuilt: …" line and does not synthesize fake events.
   */
  revisions?: EventRevision[];
  data_quality_issues?: DataQualityIssue[];
  /**
   * v28.3: structured truncation metadata.  Present on all responses;
   * indicates which sub-sections were capped at their configured limit.
   * Optional here so frontends built against older API versions still
   * type-check cleanly.
   */
  truncation?: ProvenanceTruncation | null;
}


export interface IngestionRun {
  id: string;
  source_id: string | null;
  source_name: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  records_fetched: number;
  snapshots_new: number;
  snapshots_skipped: number;
  events_created: number;
  events_updated: number;
  claims_written: number;
  projection_errors: number;
  ingestion_errors: number;
  errors: string[] | null;
}

export interface SourceStatus {
  id: string;
  short_name: string;
  display_name: string;
  tier: number;
  license_type: string;
  ingestion_enabled: boolean;
  last_ingested_at: string | null;
  latest_run_status: string | null;
  latest_run_completed_at: string | null;
  latest_run_errors: number | null;
  freshness_age_seconds: number | null;
}

export interface ArchiveManifest {
  id: string;
  archive_type: string;
  status: string;
  cutoff_at: string;
  output_uri: string;
  manifest: Record<string, unknown>;
  created_at: string;
  completed_at: string | null;
  created_by: string | null;
}

export interface AuditLogItem {
  kind: string;
  id: string;
  occurred_at: string | null;
  actor: string | null;
  event_id?: string | null;
  claim_id?: string | null;
  candidate_event_id?: string | null;
  action: string | null;
  description: string | null;
}

export interface ApiKeyRecord {
  id: string;
  operator_id: string;
  role: 'reviewer' | 'admin' | string;
  is_active: boolean;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  description: string | null;
}

export interface CreatedApiKey extends ApiKeyRecord {
  raw_key: string;
}

export interface PaginatedAccidents {
  items: AccidentSummary[];
  total: number;
  page: number;
  page_size: number;
  has_next: boolean;
  /** Opaque cursor for keyset pagination — pass as ?cursor= on the next request. */
  next_cursor?: string | null;
}

export interface SearchFilters {
  q: string;
  severity: string;
  phase: string;
  year_from: string;
  /** Minimum source completeness score filter (0.0–1.0). API also accepts legacy min_confidence. */
  min_source_completeness: string;
  fatality_status: string;  // '' | 'some' | 'none' | 'unknown'
  registration?: string;
  aircraft_type?: string;
  operator?: string;
  source_id?: string;
  disputed_only?: boolean;
  final_report_only?: boolean;
  sort: string;
}

export interface MapAccident {
  id: string;
  canonical_id: string;
  location_lat: number;
  location_lon: number;
  location_text: string | null;
  injury_severity: string | null;
  fatalities_total: number | null;
  aircraft_make: string | null;
  aircraft_model: string | null;
  occurred_date: string | null;
  occurred_year: number | null;
  phase_of_flight: string | null;
  /** Source completeness score (was confidence_score in earlier API versions) */
  source_completeness_score: number | null;
}

export interface MapCluster {
  cluster_id: string;
  location_lat: number;
  location_lon: number;
  count: number;
  fatalities_total: number;
  latest_occurred_year: number | null;
  cell_degrees: number;
}

export interface AnalyticsSummary {
  total_accidents: number;
  total_fatalities: number;
  fatal_count: number;
  /** @deprecated use avg_source_completeness */
  avg_confidence: number;
  by_severity: Record<string, number>;
  by_phase: Record<string, number>;
  by_year: Record<number, number>;
  /** @deprecated use source_completeness_bins */
  confidence_bins: Record<string, number>;
  /** Preferred alias for avg_confidence (serialized via Pydantic @computed_field) */
  avg_source_completeness?: number;
  /** Preferred alias for confidence_bins (serialized via Pydantic @computed_field) */
  source_completeness_bins?: Record<string, number>;
}

/** One row in the global conflict review queue. */
export interface ConflictQueueItem {
  conflict_id: string;
  event_id: string;
  canonical_id: string | null;
  field_name: string;
  claim_a_id: string;
  claim_b_id: string;
  claim_a_value: string | null;
  claim_b_value: string | null;
  claim_a_source: string | null;
  claim_b_source: string | null;
  created_at: string;
  occurred_date: string | null;
  location_text: string | null;
  injury_severity: string | null;
}

// ── Accident Timeline Reconstruction ─────────────────────────────────────────

export type TimePrecision =
  | 'exact'
  | 'approximate'
  | 'relative'
  | 'sequence_only'
  | 'unknown';

export interface TimelineClaimSummary {
  claim_id: string;
  field_name: string;
  claim_type: string;
  source_id: string;
  link_reason: string;
}

export interface TimelineEvent {
  id: string;
  accident_event_id: string;
  event_type: string;
  title: string;
  description: string | null;
  category: string | null;
  phase_of_flight: string | null;

  /** Absolute UTC time — only display as exact when time_precision === 'exact' */
  event_time_utc: string | null;
  /** Local accident-site time (timezone-naive) */
  event_time_local: string | null;
  /** Signed seconds relative to impact; negative = before impact */
  relative_offset_seconds: number | null;
  sequence_index: number | null;
  time_precision: TimePrecision;

  severity: string | null;
  confidence_score: number | null;
  is_disputed: boolean;
  dispute_summary: string | null;
  source_count: number;
  created_at: string;
  updated_at: string;

  supporting_claims: TimelineClaimSummary[];
}

export interface AccidentTimeline {
  accident_event_id: string;
  event_count: number;
  events: TimelineEvent[];
}

// ── Weather Context Integration ───────────────────────────────────────────────

export type FlightRules = 'vfr' | 'mvfr' | 'ifr' | 'lifr' | 'unknown';
export type IcingRisk = 'none' | 'possible' | 'likely' | 'severe' | 'unknown';
export type TurbulenceRisk = 'none' | 'possible' | 'likely' | 'severe' | 'unknown';
export type WeatherReportType =
  | 'metar' | 'taf' | 'pirep' | 'radar' | 'satellite' | 'report_summary' | 'manual';

export interface WeatherClaimSummary {
  claim_id: string;
  field_name: string;
  claim_type: string;
  source_id: string;
  link_reason: string;
}

export interface WeatherObservation {
  id: string;
  accident_event_id: string;
  source_id: string | null;

  station_identifier: string | null;
  station_name: string | null;
  station_latitude: number | null;
  station_longitude: number | null;
  distance_to_accident_km: number | null;

  observation_time_utc: string | null;
  /** Negative = observation before accident */
  accident_time_delta_minutes: number | null;

  report_type: WeatherReportType;
  /** Verbatim original report text */
  raw_report_text: string | null;
  parsed_data: Record<string, unknown> | null;

  temperature_c: number | null;
  dew_point_c: number | null;
  wind_direction_degrees: number | null;
  wind_speed_kt: number | null;
  wind_gust_kt: number | null;
  /** Visibility in metres */
  visibility_m: number | null;
  /** Lowest broken/overcast layer in feet AGL */
  ceiling_ft: number | null;
  /** Altimeter in hPa */
  altimeter_hpa: number | null;
  precipitation_type: string | null;
  thunderstorm_present: boolean | null;
  icing_risk: IcingRisk | null;
  turbulence_risk: TurbulenceRisk | null;
  flight_rules: FlightRules | null;

  confidence_score: number | null;
  is_disputed: boolean;
  dispute_summary: string | null;

  created_at: string;
  updated_at: string;

  supporting_claims: WeatherClaimSummary[];
  causation_note: string;
}

export interface WeatherContext {
  accident_event_id: string;
  observation_count: number;
  observations: WeatherObservation[];
}

// ── Mechanical / System Failure Tracking ─────────────────────────────────────

export type FailureStatus =
  | 'suspected' | 'reported' | 'confirmed' | 'disputed' | 'ruled_out' | 'unknown';

export type FailureCategory =
  | 'engine' | 'fuel' | 'hydraulic' | 'electrical' | 'avionics'
  | 'flight_controls' | 'landing_gear' | 'brakes' | 'tires' | 'structure'
  | 'pressurization' | 'navigation' | 'autopilot' | 'rotor_system'
  | 'propeller' | 'maintenance' | 'other' | 'unknown';

export type FailureSeverity = 'minor' | 'major' | 'hazardous' | 'catastrophic' | 'unknown';

export interface FailureClaimSummary {
  claim_id: string;
  field_name: string;
  claim_type: string;
  source_id: string;
  link_reason: string;
}

export interface SystemFailure {
  id: string;
  accident_event_id: string;
  source_id: string | null;

  failure_category: FailureCategory;
  subsystem: string | null;
  component_name: string | null;
  manufacturer: string | null;
  model_number: string | null;
  part_number: string | null;
  serial_number: string | null;
  failure_mode: string | null;

  status: FailureStatus;
  severity: FailureSeverity | null;
  /** True ONLY when a source explicitly asserts this failure caused the accident */
  is_causal_factor: boolean;

  occurred_in_flight: boolean | null;
  detected_before_accident: boolean | null;
  detected_during_flight: boolean | null;
  detected_post_accident: boolean | null;
  maintenance_related: boolean | null;
  inspection_finding: string | null;
  description: string | null;

  confidence_score: number | null;
  is_disputed: boolean;
  dispute_summary: string | null;
  source_count: number;

  created_at: string;
  updated_at: string;

  supporting_claims: FailureClaimSummary[];
  /** API-supplied note clarifying epistemic status — always display this */
  display_note: string;
}

export interface SystemFailures {
  accident_event_id: string;
  failure_count: number;
  failures: SystemFailure[];
}

// ── Advanced Analytics & Pattern Detection ────────────────────────────────────

export interface AdvancedSummary {
  total_accidents: number;
  fatal_accidents: number;
  disputed_records: number;
  low_confidence_records: number;
  avg_confidence: number;
  filters_applied: Record<string, unknown>;
  computation_note: string;
}

export interface SimilarAccident {
  accident_id: string;
  similarity_score: number;
  shared_factors: string[];
  differing_factors: string[];
  similarity_reasons: Record<string, number>;
  confidence_score: number;
  low_confidence_warning: boolean;
  similarity_note: string;
}

export interface SimilarAccidentsResult {
  accident_id: string;
  similar_count: number;
  similar_accidents: SimilarAccident[];
  similarity_note: string;
}

export interface DataQualitySummary {
  total_records: number;
  missing_date: number;
  missing_location: number;
  missing_aircraft_model: number;
  has_conflicts: number;
  low_confidence_records: number;
  single_source_records: number;
  preliminary_only_records: number;
  quality_note: string;
}

export interface SystemFailurePatterns {
  total_failure_records: number;
  by_category: Record<string, Record<string, number>>;
  confirmed_causal_count: number;
  confirmed_maintenance_related_count: number;
  status_note: string;
}

// ── Flight Path Reconstruction ────────────────────────────────────────────────

export interface FlightPathPoint {
  id: string;
  sequence_index: number | null;
  recorded_time_utc: string | null;
  relative_offset_seconds: number | null;
  time_precision: string;
  latitude: number | null;
  longitude: number | null;
  altitude_ft: number | null;
  altitude_reference: string | null;
  radio_altitude_ft: number | null;
  ground_speed_kt: number | null;
  indicated_airspeed_kt: number | null;
  vertical_speed_fpm: number | null;
  heading_degrees: number | null;
  track_degrees: number | null;
  distance_to_impact_km: number | null;
  uncertainty_radius_m: number | null;
  point_type: string;
  source_method: string | null;
  confidence_score: number | null;
  is_disputed: boolean;
  dispute_summary: string | null;
  notes: string | null;
  supporting_claims: Array<{ claim_id: string; field_name: string; claim_type: string; source_id: string; link_reason: string }>;
  /** True when point is inferred/estimated — must NOT be rendered as a confirmed recorded position */
  is_estimated: boolean;
}

export interface FlightPathSegment {
  id: string;
  start_point_id: string | null;
  end_point_id: string | null;
  segment_type: string;
  length_km: number | null;
  bearing_degrees: number | null;
  confidence_score: number | null;
  is_disputed: boolean;
  uncertainty_summary: string | null;
  /** "solid_recorded" | "dashed_estimated" | "disputed" | "unknown" */
  render_style: string;
}

export interface FlightPathAnnotation {
  id: string;
  flight_path_point_id: string | null;
  timeline_event_id: string | null;
  annotation_time_utc: string | null;
  relative_offset_seconds: number | null;
  time_precision: string;
  annotation_type: string;
  title: string;
  description: string | null;
  altitude_ft: number | null;
  radio_altitude_ft: number | null;
  confidence_score: number | null;
  is_disputed: boolean;
  dispute_summary: string | null;
  supporting_claims: Array<{ claim_id: string; field_name: string; claim_type: string; link_reason: string }>;
}

export interface FlightPathReconstruction {
  accident_event_id: string;
  point_count: number;
  has_path: boolean;
  accident_site: { latitude: number; longitude: number } | null;
  last_recorded_point_id: string | null;
  impact_point_id: string | null;
  bounds: { min_lat: number; max_lat: number; min_lon: number; max_lon: number } | null;
  path_length_km: number;
  confidence_summary: { avg_confidence: number | null; disputed_point_count: number; point_count: number };
  points: FlightPathPoint[];
  segments: FlightPathSegment[];
  annotations: FlightPathAnnotation[];
  data_note: string;
}

export interface FlightPathProfilePoint {
  point_id: string;
  x: string;
  x_type: string;
  point_type: string;
  time_precision: string;
  is_estimated: boolean;
  is_disputed: boolean;
  confidence_score: number | null;
  altitude_ft?: number | null;
  ground_speed_kt?: number | null;
  indicated_airspeed_kt?: number | null;
  vertical_speed_fpm?: number | null;
  distance_to_impact_km?: number | null;
}

export interface FlightPathProfile {
  accident_event_id: string;
  altitude: FlightPathProfilePoint[];
  speed: FlightPathProfilePoint[];
  vertical_speed: FlightPathProfilePoint[];
  distance_to_impact: FlightPathProfilePoint[];
  chart_note: string;
}
