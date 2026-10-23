import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { Icon } from '../../design-system/Icon';
import { listInbox, listRuns, listSources } from '../../lib/v1/api';
import { getMyHome } from '../../lib/v1/home';
import { useV1 } from '../../lib/v1/useV1';
import { AgentActivity } from './AgentActivity';
import { AskBar } from './AskBar';
import { DataToday } from './DataToday';
import { MobileHome } from './MobileHome';
import { MyMetrics } from './MyMetrics';
import { NeedsYouCard } from './NeedsYouCard';
import { recentQuestions } from './api';

/*
 * "v3.1 · Trang chủ" (2132:15338, UC-01-01, UC-08-05): the page everyone lands on.
 * Two columns under 16×32 padding with a 24px gap: the main column (greeting, question bar,
 * the metrics the viewer follows, what agents are doing, today's data) and a 380px right column
 * (what needs you, the guide). Narrower, the columns stack (1280, 1024, 768
 * tablet frames); under 768px the page is the mobile read (MobileHome, 2231:15114).
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
  const { user, dataSpace, navigate } = useAppState();
  const inbox = useV1(['listInbox', { status: 'open' }], () => listInbox({ status: 'open' }));
  const runs = useV1(['listRuns'], () => listRuns());
  const sources = useV1(['listSources'], listSources);
  const home = useV1(['getMyHome'], getMyHome);
  const [suggestions, setSuggestions] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    recentQuestions(4).then((titles) => {
      if (!cancelled) setSuggestions(titles);
    });
    return () => {
      cancelled = true;
    };
  }, [dataSpace?.id]);

  const now = new Date();
  const dateLine = `${WEEKDAYS[now.getDay()]}, ${String(now.getDate()).padStart(2, '0')}/${String(now.getMonth() + 1).padStart(2, '0')}`;

  const ask = (question: string) => {
    window.history.replaceState(null, '', `/analysis/ask?q=${encodeURIComponent(question)}`);
    navigate('analysis', 'ASK');
  };

  return (
    <>
      <div className="min-[768px]:hidden">
        <MobileHome
          name={givenName(user.name)}
          home={home.data}
          inbox={inbox.data?.items ?? []}
          runs={runs.data?.items ?? []}
          onNeedsYou={() => navigate('needs-you')}
          onRuns={() => navigate('agents', 'RUNS')}
        />
      </div>
      <div className="flex items-start gap-da-2xl px-da-3xl py-da-xl max-[1279px]:flex-col max-[767px]:hidden">
        <div className="flex min-w-0 flex-1 flex-col gap-[20px] max-[1279px]:w-full">
          <header className="flex flex-col gap-da-xs">
            <p className="uppercase text-muted-foreground da-overline">
              {dateLine} · {dataSpace?.name ?? 'Toàn tổ chức'}
            </p>
            <h1 className="text-foreground da-heading-1">
              {greetingFor(now.getHours())}, {givenName(user.name)}
            </h1>
          </header>
          <AskBar suggestions={suggestions} onAsk={ask} />
          {home.data && (
            <MyMetrics
              metrics={home.data.metrics}
              onOpen={(id) => navigate('data', 'METRIC_CATALOG', id)}
            />
          )}
          <AgentActivity runs={runs} onOpenAll={() => navigate('agents', 'RUNS')} />
          <DataToday sources={sources} onManage={() => navigate('data')} />
        </div>

        <aside className="flex w-[380px] shrink-0 flex-col gap-da-xl max-[1279px]:w-full">
          <NeedsYouCard
            inbox={inbox}
            onOpen={() => navigate('needs-you')}
            onOpenDetail={(detail) => navigate(detail.area, detail.page, detail.id)}
          />
          <button
            type="button"
            onClick={() => navigate('home', 'HOW_IT_WORKS')}
            className="flex items-start gap-da-sm rounded-lg border border-border bg-card p-da-lg text-left text-foreground da-body-small hover:border-border-strong"
          >
            <Icon name="eye" className="mt-[2px] text-primary" />
            Mới dùng? Xem Data Agent hoạt động thế nào trong 2 phút.
          </button>
        </aside>
      </div>
    </>
  );
}
