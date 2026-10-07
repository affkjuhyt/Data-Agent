/**
 * Errors from the V2 contract API (indicator-api), with the HTTP status and the
 * contract `ErrorCode` kept — screens branch on the code (VERSION_CONFLICT →
 * reload and show what changed, ACCESS_DENIED → "no permission"), never on the
 * message text. Body shape: `ErrorBody` in libs/contracts/v1/errors.py.
 */
import type { components } from './contracts';

type ErrorCode = components['schemas']['ErrorCode'];
type ErrorBody = components['schemas']['ErrorBody'];

function isErrorBody(body: unknown): body is ErrorBody {
  if (!body || typeof body !== 'object') return false;
  const error = (body as { error?: unknown }).error;
  return (
    !!error && typeof error === 'object' && typeof (error as { code?: unknown }).code === 'string'
  );
}

export class ApiError extends Error {
  readonly status: number;
  /** ErrorCode is an open enum: a newer backend may send a value this build does not list. */
  readonly code: ErrorCode | (string & {}) | undefined;
  /** Set on VERSION_CONFLICT: the version the record is at now. */
  readonly currentVersion: number | null;
  readonly details: Record<string, unknown>;

  constructor(status: number, statusText: string, body?: unknown) {
    const detail = isErrorBody(body) ? body.error : undefined;
    super(detail?.message || `${status} ${statusText}`.trim());
    this.name = 'ApiError';
    this.status = status;
    this.code = detail?.code;
    this.currentVersion = detail?.current_version ?? null;
    this.details = (detail?.details as Record<string, unknown> | undefined) ?? {};
  }
}

export const isVersionConflict = (error: unknown): error is ApiError =>
  error instanceof ApiError && error.code === 'VERSION_CONFLICT';
