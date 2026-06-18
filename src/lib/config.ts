/**
 * Central frontend configuration.
 *
 * Service base URLs are read once from Vite env here instead of being
 * re-declared (with a localhost fallback) in every component. Override via the
 * VITE_* env vars documented in .env.example.
 */
const env = (import.meta as any).env ?? {};

/** Kong gateway — KB backend API plus the release/auth proxy paths. */
export const API_URL = (env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

/** flow-builder service — workflow/agent design (versions, canvas). */
export const FLOW_BUILDER_URL = (env.VITE_FLOW_BUILDER_URL as string | undefined) ?? 'http://localhost:8002';

/** workflow-runtime service — agent execution, node registry, runtime config. */
export const WORKFLOW_RUNTIME_URL = (env.VITE_WORKFLOW_RUNTIME_URL as string | undefined) ?? 'http://localhost:8001';
