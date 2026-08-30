/// <reference types="vite/client" />
import Keycloak from 'keycloak-js';

// Store on window so Vite HMR module reloads don't create a second instance
const w = window as Window & { __kc?: Keycloak };
if (!w.__kc) {
  w.__kc = new Keycloak({
    url:      import.meta.env.VITE_KEYCLOAK_URL    || 'http://localhost:8080',
    realm:    import.meta.env.VITE_KEYCLOAK_REALM  || 'aeroflow',
    clientId: import.meta.env.VITE_KEYCLOAK_CLIENT || 'aeroflow-frontend',
  });
}

export default w.__kc;
