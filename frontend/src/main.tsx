import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import { AuthProvider } from './lib/AuthProvider.tsx';
import './index.css';

/*
 * VITE_MOCK_V1=1 (dev only) answers /v1 calls from src/mocks until the backend serves the v1
 * API of the system design. Every other request still goes to the real gateway, with real
 * Keycloak auth. The import is behind import.meta.env.DEV, so production builds drop it.
 */
async function startMocks() {
  if (import.meta.env.DEV && import.meta.env.VITE_MOCK_V1 === '1') {
    const { worker } = await import('./mocks/browser');
    await worker.start({ onUnhandledRequest: 'bypass', quiet: true });
  }
}

startMocks().then(() =>
  createRoot(document.getElementById('root')!).render(
    <AuthProvider>
      <App />
    </AuthProvider>,
  ),
);
