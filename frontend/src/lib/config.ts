/**
 * Central frontend configuration.
 *
 * Service base URLs are read once from Vite env here instead of being
 * re-declared (with a localhost fallback) in every component. Override via the
 * VITE_* env vars documented in .env.example.
 */
const env = (import.meta as any).env ?? {};

/** Kong gateway — KB backend API plus the auth proxy paths. */
export const API_URL = (env.VITE_API_URL as string | undefined) ?? 'http://localhost:8100';

/** workflow_runtime — sandbox job APIs. Empty = relative (dev proxy routes it). */
export const WORKFLOW_RUNTIME_URL = (env.VITE_WORKFLOW_RUNTIME_URL as string | undefined) ?? '';


/**
 * indicator-api (V2 contract). No default on purpose: Kong has no route for it
 * yet and the frontend pod gets VITE_* at start, so a localhost fallback would
 * point every user's browser at their own machine. Unset = V2 calls fail with a
 * clear error and the notification inbox stays off.
 */
export const INDICATOR_API_URL = (env.VITE_INDICATOR_API_URL as string | undefined) ?? '';
