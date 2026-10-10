import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const login = vi.hoisted(() => vi.fn());
vi.mock('../../lib/keycloak', () => ({ default: { login } }));

/* The page reads its env at import, so each case imports it fresh. */
async function renderLogin(env: Record<string, string>) {
  for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);
  vi.resetModules();
  const { LoginPage } = await import('./LoginPage');
  render(<LoginPage />);
}

beforeEach(() => login.mockReset());
afterEach(() => vi.unstubAllEnvs());

describe('LoginPage', () => {
  it('sends Entra ID straight to the identity provider, the AD account to Keycloak', async () => {
    await renderLogin({ VITE_KEYCLOAK_ENTRA_IDP: 'azure-ad', VITE_ENVIRONMENT_LABEL: 'Production' });
    expect(screen.getByRole('heading', { name: 'Đăng nhập' })).toBeInTheDocument();
    expect(screen.getByText(/môi trường Production/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Đăng nhập với Microsoft Entra ID' }));
    expect(login).toHaveBeenLastCalledWith(expect.objectContaining({ idpHint: 'azure-ad' }));

    fireEvent.click(screen.getByRole('button', { name: 'Đăng nhập bằng tài khoản AD (LDAP)' }));
    expect(login).toHaveBeenLastCalledWith(expect.not.objectContaining({ idpHint: expect.anything() }));
  });

  it('draws no Entra button when no provider is configured', async () => {
    await renderLogin({ VITE_KEYCLOAK_ENTRA_IDP: '', VITE_ENVIRONMENT_LABEL: '' });
    expect(screen.queryByRole('button', { name: 'Đăng nhập với Microsoft Entra ID' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Đăng nhập bằng tài khoản AD (LDAP)' })).toBeInTheDocument();
    expect(screen.queryByText(/môi trường/)).toBeNull();
  });

  it('never asks for a password itself', async () => {
    await renderLogin({ VITE_KEYCLOAK_ENTRA_IDP: 'azure-ad' });
    expect(document.querySelector('input')).toBeNull();
  });
});
