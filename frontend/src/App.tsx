import React from 'react';
import { AppStateProvider, useAppState } from './AppStateContext';
import { AppShell } from './components/shell/AppShell';
import type { AreaId } from './components/shell/areas';
import IngestionProgressOverlay from './components/shared/IngestionProgressOverlay';
import { ForbiddenToast } from './components/shared/ForbiddenToast';
import { EmptyState } from './design-system/EmptyState';
import { ToastProvider } from './design-system/Toast';
import { SkeletonRows } from './design-system/SkeletonRow';
import { useDelayedFlag } from './design-system/useDelayedFlag';
import { ROUTE_MAP, SINGLE_SCREEN } from './routes';
import { firstPageOf } from './constants';

/* Under 300 ms nothing shows (no flash); after that a skeleton in the page's place. */
const RouteLoading = () => {
  const show = useDelayedFlag(true);
  return show ? (
    <div className="px-da-3xl py-da-2xl">
      <SkeletonRows label="Đang tải màn hình…" />
    </div>
  ) : null;
};

function AppContent() {
  const { activeModule, subTab } = useAppState();
  const area = activeModule as AreaId;

  // Identity is never sent from the client: the bearer token goes to Kong, which
  // injects the authoritative X-* identity headers the backend trusts.

  const renderView = () => {
    const routes = ROUTE_MAP[area];
    const page = subTab[area] ?? firstPageOf(area) ?? SINGLE_SCREEN;
    const entry = routes ? (routes[page] ?? routes[SINGLE_SCREEN]) : undefined;

    if (!entry) {
      return (
        <div className="px-da-3xl py-da-2xl">
          <EmptyState
            icon="alert"
            title="Không có màn hình này"
            description="Đường dẫn này không còn dùng. Chọn một khu vực ở thanh trên."
          />
        </div>
      );
    }
    return <React.Suspense fallback={<RouteLoading />}>{entry.render()}</React.Suspense>;
  };

  return (
    <ToastProvider>
      <AppShell>{renderView()}</AppShell>
      {/* Progress overlay - always available, visible when ingestions active */}
      <IngestionProgressOverlay />
      {/* 403 from apiClient → toast (listens to window 'app:forbidden') */}
      <ForbiddenToast />
    </ToastProvider>
  );
}

export default function App() {
  return (
    <AppStateProvider>
      <AppContent />
    </AppStateProvider>
  );
}
