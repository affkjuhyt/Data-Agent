/**
 * Canonical enum values shared across DB, backend services, and UI.
 *
 * Each value here matches:
 *   - the Postgres enum value in deploy/docker/Postgres/init.sql
 *   - the Python Enum `.value` in basemodel/services_databaseconnector/postgres_model.py
 *   - the wire/API string transmitted between backend and UI
 *
 * UI components consume these as the canonical type. Human-readable labels
 * live in the matching *_LABELS map below — never store display strings in DB.
 */

// ── KBTier ────────────────────────────────────────────────────────────────────
export const TIER_VALUES = ['bronze', 'silver', 'gold'] as const;
export type Tier = typeof TIER_VALUES[number];

// ── KBConflictType ────────────────────────────────────────────────────────────
export const CONFLICT_TYPE_VALUES = [
  'content_contradiction',
  'content_conflict',
  'content_duplicate',
  'content_update',
  'table_schema',
] as const;
export type ConflictType = typeof CONFLICT_TYPE_VALUES[number];

export const CONFLICT_TYPE_LABELS: Record<ConflictType, string> = {
  content_contradiction: 'Content Contradiction',
  content_conflict:      'Content Conflict',
  content_duplicate:     'Content Duplicate',
  content_update:        'Content Update',
  table_schema:          'Table Schema',
};

// ── KBConflictSeverity ────────────────────────────────────────────────────────
export const SEVERITY_VALUES = ['low', 'medium', 'high'] as const;
export type Severity = typeof SEVERITY_VALUES[number];

// ── KBConflictStatus ──────────────────────────────────────────────────────────
export const CONFLICT_STATUS_VALUES = ['pending', 'awaiting', 'resolved'] as const;
export type ConflictStatus = typeof CONFLICT_STATUS_VALUES[number];

// ── ConflictResolution (varchar column; values are wire identifiers) ──────────
export const RESOLUTION_VALUES = ['keep_existing', 'keep_incoming', 'merge', 'no_action'] as const;
export type ResolutionMethod = typeof RESOLUTION_VALUES[number];

export const RESOLUTION_LABELS: Record<ResolutionMethod, string> = {
  keep_existing: 'Keep Existing',
  keep_incoming: 'Keep Incoming',
  merge:         'Merge',
  no_action:     'No Action',
};

// ── KBPolicyFormat (filter policies) ──────────────────────────────────────────
export const FILTER_POLICY_TYPE_VALUES = ['natural_language', 'exact_word'] as const;
export type FilterPolicyType = typeof FILTER_POLICY_TYPE_VALUES[number];

// ── DocStatus (lifecycle; backend computes these from layer + workflow) ───────
export const DOC_STATUS_VALUES = [
  'RAW',
  'OCR_COMPLETE',
  'CLEANED',
  'CHUNKING',
  'EMBEDDING',
  'GRAPH_EXTRACTING',
  'PUBLISHED',
  'FAILED',
  'DEPRECATED',
  'ARCHIVED',
  'PENDING_APPROVAL',
] as const;
export type DocStatus = typeof DOC_STATUS_VALUES[number];
