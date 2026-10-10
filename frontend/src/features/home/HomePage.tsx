import { useAppState } from '../../AppStateContext';
import { listInbox, listRuns, listSources } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';
import { AgentActivity } from './AgentActivity';
import { AskBar } from './AskBar';
import { DataToday } from './DataToday';
import { NeedsYouCard } from './NeedsYouCard';
import { recentQuestions } from './api';

/*
 * "v3.1 · Trang chủ" (2132:15338, UC-01-01, UC-08-05): the page everyone lands on.
 * Two columns under 16×32 padding with a 24px gap: the main column (greeting, question bar,
 * what agents are doing, today's data) and a 380px right column (what needs you).
 * "Chỉ số của bạn" and "Dashboard của bạn" of the design have no v1 endpoint yet (no list of a
 * viewer's metrics with values, no /v1/dashboards); they are left out rather than invented.
 */
const WEEKDAYS = ['Chủ Nhật', 'Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy'];

function greetingFor(hour: number): string {
  if (hour < 11) return 'Chào buổi sáng';
  if (hour < 13) return 'Chào buổi trưa';
  if (hour < 18) return 'Chào buổi chiều';
  return 'Chào buổi tối';
}

/** Vietnamese names put the family name first: "Nguyễn Hải Yến" is greeted as "Hải Yến". */
function givenName(fullName: string): string {
  const words = fullName.trim().split(/\s+/);
  return words.length > 1 ? words.slice(1).join(' ') : fullName;
}

export default function HomePage() {
  const { user, tenant, navigate } = useAppState();
  const inbox = useV1(() => listInbox({ status: 'open' }), []);
  const runs = useV1(() => listRuns(), []);
  const sources = useV1(listSources, []);

  const now = new Date();
  const dateLine = `${WEEKDAYS[now.getDay()]}, ${String(now.getDate()).padStart(2, '0')}/${String(now.getMonth() + 1).padStart(2, '0')}`;

  const ask = (question: string) => {
    window.history.replaceState(null, '', `/analysis/ask?q=${encodeURIComponent(question)}`);
    navigate('analysis', 'ASK');
  };

  return (
    <div className="flex items-start gap-da-2xl px-da-3xl py-da-xl max-[1279px]:flex-col">
      <div className="flex min-w-0 flex-1 flex-col gap-[20px] max-[1279px]:w-full">
        <header className="flex flex-col gap-da-xs">
          <p className="uppercase text-muted-foreground da-overline">
            {dateLine} · {tenant.tenantName ?? 'Toàn tổ chức'}
          </p>
          <h1 className="text-foreground da-heading-1">
            {greetingFor(now.getHours())}, {givenName(user.name)}
          </h1>
        </header>
        <AskBar suggestions={recentQuestions(4)} onAsk={ask} />
        <AgentActivity runs={runs} onOpenAll={() => navigate('agents', 'RUNS')} />
        <DataToday sources={sources} onManage={() => navigate('data')} />
      </div>

      <aside className="flex w-[380px] shrink-0 flex-col gap-da-xl max-[1279px]:w-full">
        <NeedsYouCard
          inbox={inbox}
          onOpen={() => navigate('needs-you')}
          onOpenDetail={(detail) => navigate(detail.area, detail.page, detail.id)}
        />
      </aside>
    </div>
  );
}
