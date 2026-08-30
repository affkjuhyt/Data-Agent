/**
 * Custom RTL render that wraps a component in the providers needed across
 * the app. Add new providers here as features grow — keeps individual tests
 * from having to mount the whole tree.
 */
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

type ProvidersProps = { children: ReactNode };

function AllProviders({ children }: ProvidersProps) {
  // TODO: wrap in AppStateProvider + AuthProvider once we have mock impls
  // that don't require a live Keycloak. For now this is a pass-through so
  // the foundation is committable and grows incrementally.
  return <>{children}</>;
}

export function renderWithProviders(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
) {
  return render(ui, { wrapper: AllProviders, ...options });
}

export * from "@testing-library/react";
export { renderWithProviders as render };
