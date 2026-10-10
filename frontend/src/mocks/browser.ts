import { setupWorker } from 'msw/browser';
import { createHandlers } from './handlers';

/** The dev-only v1 mock (VITE_MOCK_V1=1); started from main.tsx. */
export const worker = setupWorker(...createHandlers());
