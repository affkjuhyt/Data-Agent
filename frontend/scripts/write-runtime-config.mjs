// Writes dist/runtime/config.js from the container's environment when it starts (GEN-05), so one
// production build serves every install: the URLs and names come from the frontend-env ConfigMap
// and frontend-secrets, as they did for the dev server. JSON.stringify keeps any value a literal.
import { mkdirSync, writeFileSync } from 'node:fs';

const NAMES = [
  'VITE_API_URL',
  'VITE_KEYCLOAK_URL',
  'VITE_KEYCLOAK_REALM',
  'VITE_KEYCLOAK_CLIENT',
  'VITE_KEYCLOAK_ENTRA_IDP',
  'VITE_ENVIRONMENT_LABEL',
];
const settings = Object.fromEntries(
  NAMES.filter((name) => process.env[name]).map((name) => [name, process.env[name]]),
);
mkdirSync('dist/runtime', { recursive: true });
writeFileSync(
  'dist/runtime/config.js',
  `window.__DATA_AGENT_CONFIG__ = ${JSON.stringify(settings)};\n`,
);
console.log(`runtime config: ${Object.keys(settings).join(', ') || 'none'}`);
