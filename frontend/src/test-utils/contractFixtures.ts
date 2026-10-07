/**
 * The Contract v1 mock's fixtures (tests/fixtures/contracts/mock, NOP-151), read
 * as they are on disk: frontend contract tests use the same bodies the mock
 * server and the backend contract tests use, not copies.
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Plain paths: under jsdom the global URL is jsdom's, which node:fs rejects.
export const MOCK_FIXTURES_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../../tests/fixtures/contracts/mock',
);

export const mockFixture = <T = unknown>(path: string): T =>
  JSON.parse(readFileSync(resolve(MOCK_FIXTURES_DIR, path), 'utf-8')) as T;
