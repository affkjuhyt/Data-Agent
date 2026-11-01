/// <reference types="vite/client" />
import Keycloak from 'keycloak-js';
import { setting } from './runtimeConfig';

// Store on window so Vite HMR module reloads don't create a second instance
const w = window as Window & { __kc?: Keycloak };
const keycloak: Keycloak = (w.__kc ??= new Keycloak({
  url: setting('VITE_KEYCLOAK_URL') || 'http://localhost:30110',
  realm: setting('VITE_KEYCLOAK_REALM') || 'aeroflow',
  clientId: setting('VITE_KEYCLOAK_CLIENT') || 'aeroflow-frontend',
}));

export default keycloak;
