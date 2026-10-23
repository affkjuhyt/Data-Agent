import { Fragment, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Icon, type IconName } from '../../design-system/Icon';
import { LinkCard } from '../../design-system/LinkCard';
import { StatusDot } from '../../design-system/StatusDot';
import { listAgents, listInbox, listMetrics, listSources, listTerms } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Data Agent hoạt động thế nào [toàn cục]" (2132:15151): the two-minute guide shown
 * after onboarding and reachable from Trang chủ. The five-step journey (each card opens the
 * area that does it, with the viewer's own count from the v1 lists, hidden until loaded), the
 * three ways to the same certified figure, and the two ways to start. The "1,84% · NPL_RATIO
 * v4" lines are the guide's worked example, not live data.
 */
const SAMPLE_QUESTION = 'Nợ xấu bán lẻ quý 3 là bao nhiêu?';

export default function HowItWorksPage() {
  const { navigate } = useAppState();
  const sources = useV1(['listSources'], listSources);
  const metrics = useV1(['listMetrics'], listMetrics);
  const terms = useV1(['listTerms'], listTerms);
  const agents = useV1(['listAgents'], listAgents);
  const inbox = useV1(['listInbox', { status: 'open' }], () => listInbox({ status: 'open' }));

  const count = (n: number | undefined, text: string) =>
    n === undefined ? undefined : `${n.toLocaleString('vi-VN')} ${text}`;
  const running = agents.data?.items.filter((a) => a.status === 'running').length;

  const steps: {
    icon: IconName;
    title: string;
    text: string;
    badge?: string;
    open: () => void;
  }[] = [
    {
      icon: 'database',
      title: 'Kết nối',
      text: 'Chọn nguồn: cơ sở dữ liệu, kho dữ liệu, API, tệp, tài liệu. Hệ thống kiểm tra và chỉ đọc.',
      badge: count(sources.data?.items.length, 'nguồn đã kết nối'),
      open: () => navigate('data', 'SOURCES'),
    },
    {
      icon: 'layers',
      title: 'Chuẩn bị',
      text: 'Chỉ số và thuật ngữ được định nghĩa một lần, có người chứng nhận.',
      badge:
        metrics.data && terms.data
          ? `${metrics.data.items.length.toLocaleString('vi-VN')} chỉ số · ${terms.data.items.length.toLocaleString('vi-VN')} thuật ngữ`
          : undefined,
      open: () => navigate('data', 'METRIC_CATALOG'),
    },
    {
      icon: 'search',
      title: 'Khám phá',
      text: 'Hỏi bằng lời hoặc kéo thả chỉ số, chiều. Bấm vào số để xem nguồn gốc.',
      badge: 'Không cần SQL',
      open: () => navigate('analysis', 'EXPLORE'),
    },
    {
      icon: 'agent',
      title: 'Agent làm việc',
      text: 'Agent tự theo dõi, điều tra, đối soát, soạn báo cáo theo lịch hoặc sự kiện.',
      badge: count(running, 'agent đang chạy'),
      open: () => navigate('agents', 'TEAM'),
    },
    {
      icon: 'approve',
      title: 'Bạn quyết định',
      text: 'Agent dừng hỏi khi cần. Quyết định của bạn có thể thành quy tắc cho lần sau.',
      badge: count(inbox.data?.items.length, 'việc chờ bạn'),
      open: () => navigate('needs-you'),
    },
  ];

  const askSample = () => {
    window.history.replaceState(null, '', `/analysis/ask?q=${encodeURIComponent(SAMPLE_QUESTION)}`);
    navigate('analysis', 'ASK');
  };

  return (
    <div className="flex min-h-full flex-col items-center gap-[28px] px-da-3xl py-da-2xl">
      <header className="flex w-[880px] max-w-full flex-col items-center gap-da-lg text-center">
        <StatusDot tone="primary">Hướng dẫn 2 phút</StatusDot>
        <h1 className="text-foreground da-heading-1">
          Dữ liệu của bạn, các agent làm việc, bạn ra quyết định
        </h1>
        <p className="text-muted-foreground da-reading">
          Kết nối dữ liệu một lần. Định nghĩa chỉ số một lần. Sau đó hỏi bằng lời, kéo thả để khám
          phá, hoặc giao cho agent làm thường xuyên — mọi cách đều ra cùng một con số.
        </p>
      </header>

      <ol className="flex w-full items-stretch gap-da-lg max-[1279px]:grid max-[1279px]:grid-cols-2">
        {steps.map((s, i) => (
          <Fragment key={s.title}>
            {i > 0 && (
              <li aria-hidden className="flex items-center text-border-strong max-[1279px]:hidden">
                <Icon name="chevron-right" />
              </li>
            )}
            <li className="flex min-w-0 flex-1">
              <LinkCard
                icon={s.icon}
                iconSlot={40}
                corner={i + 1}
                title={s.title}
                titleSize="md"
                onOpen={s.open}
              >
                <span className="text-muted-foreground da-body-small">{s.text}</span>
                {s.badge && <Badge>{s.badge}</Badge>}
              </LinkCard>
            </li>
          </Fragment>
        ))}
      </ol>

      <div className="flex w-full gap-da-xl max-[1279px]:flex-col">
        <Way icon="chat" title="Hỏi bằng lời">
          <p className="rounded-lg bg-subtle px-da-md py-da-sm text-foreground da-body">
            {SAMPLE_QUESTION}
          </p>
        </Way>
        <Way icon="chart" title="Kéo thả">
          <span className="flex flex-wrap gap-da-sm">
            <StatusDot tone="primary">Chỉ số: Nợ xấu</StatusDot>
            <StatusDot tone="primary">Chiều: Quý</StatusDot>
            <StatusDot tone="primary">Lọc: Bán lẻ</StatusDot>
          </span>
        </Way>
        <Way icon="agent" title="Agent báo cáo">
          <p className="text-foreground da-body">Bản tin tuần 41: “Nợ xấu bán lẻ 1,84%…”</p>
        </Way>
      </div>

      <p className="flex items-center gap-da-md text-foreground da-body-strong">
        <Icon name="check" className="text-primary" />
        Ba cách, cùng một định nghĩa chỉ số đã chứng nhận — nên các con số luôn khớp nhau.
      </p>

      <div className="flex gap-da-md">
        <Button kind="primary" onClick={() => navigate('data', 'CONNECT')}>
          Bắt đầu: kết nối nguồn đầu tiên
        </Button>
        <Button onClick={askSample}>Thử hỏi với dữ liệu mẫu</Button>
      </div>
    </div>
  );
}

function Way({ icon, title, children }: { icon: IconName; title: string; children: ReactNode }) {
  return (
    <section className="flex min-w-0 flex-1 flex-col gap-da-lg rounded-lg border border-border bg-card px-da-xl py-da-lg">
      <h2 className="flex items-center gap-da-md text-foreground da-heading-3">
        <Icon name={icon} />
        {title}
      </h2>
      {children}
      <p className="text-primary da-body-strong">→ 1,84% · NPL_RATIO v4</p>
    </section>
  );
}
