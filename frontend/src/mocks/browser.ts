import { setupWorker } from 'msw/browser';
import { createHandlers } from './handlers';
import { createPassthroughHandlers } from './integrated';

/*
 * The dev-only v1 mock (VITE_MOCK_V1=1); started from main.tsx. The groups in integrated.ts go
 * to the real api first; VITE_MOCK_V1=all mocks them too, for a demo without a backend.
 */
export const worker = setupWorker(
  ...(import.meta.env.VITE_MOCK_V1 === 'all' ? [] : createPassthroughHandlers()),
  ...createHandlers(),
);
