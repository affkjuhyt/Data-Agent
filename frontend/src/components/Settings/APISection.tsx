/**
 * Settings → API & Developers.
 *
 *   api keys  <APIKeysPanel />   (Settings/Auth/APIKeysPanel — real useAPIKeys)
 *
 * No webhook registrations: nothing server-side delivers events or reads the
 * registrations, so the form configured nothing. No SDK block: there is no
 * published SDK to link to.
 */
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { Card } from '../ui';
import { APIKeysPanel } from './Auth/APIKeysPanel';

export const APISection = () => (
  <div className="space-y-8 pb-10">
    <OperationalHeader
      title="API & Developers"
      subtitle="API keys cho truy cập lập trình."
      breadcrumbs={[{ label: 'Settings' }, { label: 'Platform' }, { label: 'API' }]}
      status={<StatusBadge status="ACTIVE" size="lg" />}
    />

    <Card padding="sm" className="bg-[#111111] border-[#111111]">
      {/* APIKeysPanel is styled for a dark surface (Auth section) — keep it on one. */}
      <div className="p-3"><APIKeysPanel /></div>
    </Card>
  </div>
);
