/**
 * Canonical enum values shared across DB, backend services, and UI.
 *
 * Each value here matches:
 *   - the Postgres enum value in infra/Postgres/init.sql
 *   - the Python Enum `.value` in basemodel/services_databaseconnector/postgres_model.py
 *   - the wire/API string transmitted between backend and UI
 *
 * UI components consume these as the canonical type. Human-readable labels
 * live in the matching *_LABELS map below — never store display strings in DB.
 */

// ── KBTier ────────────────────────────────────────────────────────────────────
export const TIER_VALUES = ['bronze', 'silver', 'gold'] as const;
export type Tier = typeof TIER_VALUES[number];

export const TIER_LABELS: Record<Tier, string> = {
  bronze: 'Bronze',
  silver: 'Silver',
  gold:   'Gold',
};

// ── KBSourceType ──────────────────────────────────────────────────────────────
export const SOURCE_TYPE_VALUES = ['doc', 'web', 'image', 'video', 'warehouse'] as const;
export type SourceType = typeof SOURCE_TYPE_VALUES[number];

export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  doc:       'Document',
  web:       'Web',
  image:     'Image',
  video:     'Video',
  warehouse: 'Warehouse',
};

// ── KBLanguage ────────────────────────────────────────────────────────────────
export const LANGUAGE_VALUES = ['english', 'vietnamese'] as const;
export type Language = typeof LANGUAGE_VALUES[number];

export const LANGUAGE_LABELS: Record<Language, string> = {
  english:    'English',
  vietnamese: 'Vietnamese',
};

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

export const SEVERITY_LABELS: Record<Severity, string> = {
  low:    'Low',
  medium: 'Medium',
  high:   'High',
};

// ── KBConflictStatus ──────────────────────────────────────────────────────────
export const CONFLICT_STATUS_VALUES = ['pending', 'awaiting', 'resolved'] as const;
export type ConflictStatus = typeof CONFLICT_STATUS_VALUES[number];

export const CONFLICT_STATUS_LABELS: Record<ConflictStatus, string> = {
  pending:  'Pending',
  awaiting: 'Awaiting',
  resolved: 'Resolved',
};

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

export const FILTER_POLICY_TYPE_LABELS: Record<FilterPolicyType, string> = {
  natural_language: 'Natural Language',
  exact_word:       'Exact Match For Word or Phrase',
};

// ── KBPolicyType (extraction policies) ────────────────────────────────────────
export const EXTRACTION_POLICY_TYPE_VALUES = ['entity_node', 'relationship_edge'] as const;
export type ExtractionPolicyType = typeof EXTRACTION_POLICY_TYPE_VALUES[number];

export const EXTRACTION_POLICY_TYPE_LABELS: Record<ExtractionPolicyType, string> = {
  entity_node:       'Entity',
  relationship_edge: 'Relationship Edge',
};

// ── KBTaskType ────────────────────────────────────────────────────────────────
export const TASK_TYPE_VALUES = ['embedding', 'vision_language_model'] as const;
export type TaskType = typeof TASK_TYPE_VALUES[number];

export const TASK_TYPE_LABELS: Record<TaskType, string> = {
  embedding:             'Embedding',
  vision_language_model: 'Vision Language Model',
};

// ── KBSimilarityMetric ────────────────────────────────────────────────────────
export const SIMILARITY_METRIC_VALUES = ['cosine', 'euclidean', 'dot'] as const;
export type SimilarityMetric = typeof SIMILARITY_METRIC_VALUES[number];

// ── KBHttpMethod ──────────────────────────────────────────────────────────────
export const HTTP_METHOD_VALUES = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const;
export type HttpMethod = typeof HTTP_METHOD_VALUES[number];

// ── KBAPIType ─────────────────────────────────────────────────────────────────
export const API_TYPE_VALUES = ['NEO4J', 'QDRANT', 'RETRIEVE'] as const;
export type APIType = typeof API_TYPE_VALUES[number];

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
