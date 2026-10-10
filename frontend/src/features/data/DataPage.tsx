import { Fragment } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { LinkCard } from '../../design-system/LinkCard';
import { Notice } from '../../design-system/Notice';
import { formatElapsed } from '../../lib/formatVN';
import { listDatasets, listMetrics, listSources, listTerms } from '../../lib/v1/api';
import type { Source } from '../../lib/v1/types';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Dữ liệu" (2155:16260, UC-04-09): what agents and people answer from, as a journey
 * Kết nối → Chuẩn bị → Định nghĩa → Sẵn sàng dùng, with the health of the sources on top.
 * "Thay đổi gần đây" and "Được hỏi nhiều tuần này" of the design have no API yet.
 * Sources, uploads and collections still open the Knowledge Ops screen until P04/P05 are rebuilt.
 */
const PENDING = 'Màn này đang được làm.';

export default function DataPage() {
  const { navigate } = useAppState();
  const sources = useV1(listSources, []);
  const datasets = useV1(listDatasets, []);
  const metrics = useV1(listMetrics, []);
  const terms = useV1(listTerms, []);
  const openSources = () => navigate('data', 'SOURCES');

  const steps = [
    {
      icon: 'database' as const,
      title: 'Kết nối',
      stat: sources.data ? `${sources.data.items.length} nguồn` : null,
      text: 'Cơ sở dữ liệu, kho dữ liệu, API, tệp, email, ghi âm',
      link: 'Quản lý kết nối →',
      onOpen: openSources,
    },
    {
      icon: 'sliders' as const,
      title: 'Chuẩn bị',
      stat: null,
      text: 'Làm sạch, loại trùng, ghép bảng theo lịch',
      link: 'Xem pipeline →',
    },
    {
      icon: 'layers' as const,
      title: 'Định nghĩa',
      stat:
        metrics.data && terms.data
          ? `${metrics.data.items.length} chỉ số · ${terms.data.items.length} thuật ngữ`
          : null,
      text: 'Chỉ số đã chứng nhận, từ điển nghiệp vụ',
      link: 'Mở semantic layer →',
    },
    {
      icon: 'check' as const,
      title: 'Sẵn sàng dùng',
      stat: datasets.data
        ? `${datasets.data.items.filter((d) => d.certified).length} bộ dữ liệu`
        : null,
      text: 'Dùng trong Khám phá, dashboard và agent',
      link: 'Mở bộ dữ liệu →',
    },
  ];

  return (
    <div className="flex flex-col gap-[20px] px-da-3xl py-da-xl">
      <PageHeader
        title="Dữ liệu"
        description="Mọi thứ agent và bạn dùng để trả lời câu hỏi: nguồn đã kết nối, dữ liệu đã chuẩn bị, và định nghĩa nghiệp vụ."
        actions={
          <>
            <Button onClick={openSources}>Tải tệp</Button>
            <Button kind="primary" onClick={openSources}>
              Kết nối nguồn
            </Button>
          </>
        }
      />

      {sources.data && <SourceHealth sources={sources.data.items} onFix={openSources} />}

      <div className="flex items-stretch gap-da-lg max-[1023px]:flex-col">
        {steps.map((step, index) => (
          <Fragment key={step.title}>
            {index > 0 && (
              <span
                className="flex items-center text-border-strong max-[1023px]:hidden"
                aria-hidden
              >
                <Icon name="chevron-right" />
              </span>
            )}
            <div className="flex min-w-0 flex-1">
              <LinkCard
                icon={step.icon}
                title={step.title}
                titleSize="md"
                corner={index + 1}
                onOpen={step.onOpen}
                pendingReason={step.onOpen ? undefined : PENDING}
                className="h-full"
              >
                {step.stat && <span className="text-foreground da-body-strong">{step.stat}</span>}
                <span className="text-muted-foreground da-caption">{step.text}</span>
                <span className="text-primary da-body-strong">{step.link}</span>
              </LinkCard>
            </div>
          </Fragment>
        ))}
      </div>
    </div>
  );
}

/** "11/12 nguồn đúng lịch. DWH bán lẻ ngừng cập nhật từ 02:10 — số liệu bán lẻ có thể cũ 8 giờ." */
function SourceHealth({ sources, onFix }: { sources: Source[]; onFix: () => void }) {
  const live = sources.filter((s) => s.status !== 'archived' && s.status !== 'draft');
  const late = live.filter((s) => s.status === 'needs_fix' || (s.late_by_minutes ?? 0) > 0);
  if (live.length === 0) return null;
  const onTime = `${live.length - late.length}/${live.length} nguồn đúng lịch.`;
  if (late.length === 0) return <Notice tone="success">{onTime}</Notice>;
  const first = late[0];
  const since = first.last_refreshed_at
    ? new Date(first.last_refreshed_at).toLocaleTimeString('vi-VN', {
        hour: '2-digit',
        minute: '2-digit',
      })
    : null;
  const stale = first.late_by_minutes
    ? ` — số liệu có thể cũ ${formatElapsed(first.late_by_minutes * 60)}`
    : '';
  return (
    <Notice tone="warning" action={<Button onClick={onFix}>Sửa kết nối</Button>}>
      {onTime} {first.name} ngừng cập nhật{since ? ` từ ${since}` : ''}
      {stale}.{late.length > 1 ? ` Còn ${late.length - 1} nguồn khác cần xem.` : ''}
    </Notice>
  );
}
