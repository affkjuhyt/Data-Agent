/*
 * The install's own settings, read when the page loads rather than baked into the bundle (GEN-05):
 * one production build serves every install. The container writes them to /runtime/config.js
 * from its VITE_* environment (scripts/write-runtime-config.mjs) and index.html loads that file
 * before the app; the dev server and the tests have none, so a setting falls back to Vite's
 * import.meta.env as before.
 */
type SettingName =
  | 'VITE_API_URL'
  | 'VITE_KEYCLOAK_URL'
  | 'VITE_KEYCLOAK_REALM'
  | 'VITE_KEYCLOAK_CLIENT'
  | 'VITE_KEYCLOAK_ENTRA_IDP'
  | 'VITE_ENVIRONMENT_LABEL';

type Settings = Partial<Record<SettingName, string>>;

export function setting(name: SettingName): string | undefined {
  const fromInstall = (window as Window & { __DATA_AGENT_CONFIG__?: Settings })
    .__DATA_AGENT_CONFIG__;
  return fromInstall?.[name] || (import.meta.env[name] as string | undefined) || undefined;
}
