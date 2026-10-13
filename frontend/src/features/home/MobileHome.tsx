import { Icon } from '../../design-system/Icon';
import { StatusDot } from '../../design-system/StatusDot';
import { cn } from '../../lib/utils';
import type { MyHome } from '../../lib/v1/home';
import type { AgentRun, HitlRequest } from '../../lib/v1/types';
import { isExpiringSoon } from '../needs-you/decision';

/*
 * "v3.1 · Mobile · Trang chủ" (2231:15114, UC-01-01): under 768px the home page is a short read —
 * the agents' morning summary, the followed metrics two by two, then one card each for what needs
 * the viewer and what agents are running, and where the data stands.
 */
const ARROW = { up: '▲', down: '▼', flat: '' } as const;

export function MobileHome({
  name,
  home,
  inbox,
  runs,
  onNeedsYou,
  onRuns,
}: {
  name: string;
  home: MyHome | undefined;
  inbox: HitlRequest[];
  runs: AgentRun[];
  onNeedsYou: () => void;
  onRuns: () => void;
}) {
  const live = runs.filter((r) => r.status === 'running' || r.status === 'waiting_human');
  const lead = live[0];
  const expiring = inbox.filter((r) => isExpiringSoon(r)).length;
  return (
    <div className="flex flex-col gap-da-md px-da-md py-da-lg">
      <h1 className="text-foreground da-heading-1">Chào {name}</h1>
      {home?.briefing && (
        <section className="flex flex-col gap-da-sm rounded-lg border border-border bg-card p-da-md">
          <h2 className="flex items-center gap-da-sm text-foreground da-heading-3">
            {home.briefing.title}
            <StatusDot tone="success">AI</StatusDot>
          </h2>
          <ul className="flex flex-col gap-da-xs text-foreground da-body-small">
            {home.briefing.lines.map((l) => (
              <li key={l}>· {l}</li>
            ))}
          </ul>
        </section>
      )}
      {home && (
        <dl className="grid grid-cols-2 gap-da-sm">
          {home.metrics.slice(0, 4).map((m) => (
            <div
              key={m.metric_id}
              className="flex flex-col gap-[2px] rounded-lg border border-border bg-card p-da-md"
            >
              <dt className="text-muted-foreground da-caption">{m.short_label}</dt>
              <dd className="text-foreground da-metric">{m.value}</dd>
              <dd
                className={cn(
                  'da-caption',
                  m.delta.good
                    ? m.delta.direction === 'flat'
                      ? 'text-muted-foreground'
                      : 'text-success'
                    : m.delta.warning
                      ? 'text-warning'
                      : 'text-error',
                )}
              >
                {ARROW[m.delta.direction]} {m.delta.text}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <LinkCard title="Cần bạn" onClick={onNeedsYou}>
        {inbox.length} việc{expiring > 0 && ` · ${expiring} sắp hết hạn`}
      </LinkCard>
      <LinkCard title="Agent đang chạy" onClick={onRuns}>
        {live.length} agent
        {lead?.current_step && lead.total_steps
          ? ` · ${lead.agent_name} ở bước ${lead.current_step}/${lead.total_steps}`
          : ''}
      </LinkCard>
      {home?.data_note && <p className="text-muted-foreground da-caption">{home.data_note}</p>}
    </div>
  );
}

function LinkCard({
  title,
  onClick,
  children,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-da-sm rounded-lg border border-border bg-card p-da-md text-left"
    >
      <span className="flex min-w-0 flex-1 flex-col gap-[2px]">
        <span className="text-foreground da-heading-3">{title}</span>
        <span className="text-muted-foreground da-body-small">{children}</span>
      </span>
      <Icon name="chevron-right" className="text-muted-foreground" />
    </button>
  );
}
