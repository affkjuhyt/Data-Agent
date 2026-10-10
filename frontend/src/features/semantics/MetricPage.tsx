import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatDate } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  createMetricDraft,
  getMetric,
  previewMetricVersion,
  type MetricDetail,
  type MetricVersion,
} from '../../lib/v1/metrics';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P07 · Chỉ số · Định nghĩa" (2301:19011, UC-07-01): one metric of the context layer. The
 * definition of the version shown (by default the one in use), who owns and uses it, and its
 * versions on the right. A certified version is never edited in place: "Sửa" opens the draft
 * (or creates one from the version in use), and a draft is backtested on six periods before its
 * owner can certify it.
 */
type Tone = 'success' | 'warning' | 'info' | 'idle' | 'error';

const VERSION_STATUS: Record<MetricVersion['status'], { label: string; tone: Tone }> = {
  certified: { label: 'Đang dùng', tone: 'success' },
  superseded: { label: 'Đã thay', tone: 'idle' },
  draft: { label: 'Nháp', tone: 'warning' },
  testing: { label: 'Đang chạy thử', tone: 'info' },
  pending_review: { label: 'Chờ chứng nhận', tone: 'info' },
  broken: { label: 'Hỏng', tone: 'error' },
};

const DIRECTION: Record<MetricVersion['direction'], string> = {
  higher_better: 'Cao hơn là tốt',
  lower_better: 'Thấp hơn là tốt',
  neutral: 'Không có chiều tốt',
};

const PERIOD: Record<MetricVersion['period_semantics'], string> = {
  end_of_period: 'Số cuối kỳ',
  sum_over_period: 'Tổng trong kỳ',
  average_over_period: 'Trung bình trong kỳ',
};

const GRAIN: Record<MetricVersion['time_grain'], string> = {
  day: 'theo ngày nghiệp vụ',
  month: 'theo tháng',
  quarter: 'theo quý',
  year: 'theo năm',
};

const monthYear = (iso: string) => formatDate(iso).slice(3);

export default function MetricPage() {
  const { routeId } = useAppState();
  const metric = useV1(routeId ? () => getMetric(routeId) : null, [routeId]);
  return (
    <AsyncRegion state={metric} what="chỉ số">
      {(m) => <MetricBody metric={m} onChanged={metric.reload} />}
    </AsyncRegion>
  );
}

function MetricBody({ metric, onChanged }: { metric: MetricDetail; onChanged: () => void }) {
  const toast = useToast();
  const inUse = metric.versions.find((v) => v.status === 'certified');
  const draft = metric.versions.find((v) => v.status === 'draft' || v.status === 'testing');
  const [shownId, setShownId] = useState(inUse?.id ?? metric.versions[0]?.id);
  const shown = metric.versions.find((v) => v.id === shownId) ?? inUse ?? metric.versions[0];
  const [busy, setBusy] = useState<'draft' | 'preview' | null>(null);
  const [error, setError] = useState<string>();

  const edit = async () => {
    if (draft) {
      setShownId(draft.id);
      return;
    }
    if (!inUse) return;
    setBusy('draft');
    setError(undefined);
    try {
      const created = await createMetricDraft(metric.id, inUse, 'Bản nháp mới');
      toast({ tone: 'success', message: `Đã tạo bản nháp v${created.version}` });
      setShownId(created.id);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const preview = async (version: MetricVersion) => {
    setBusy('preview');
    setError(undefined);
    try {
      await previewMetricVersion(version.id);
      toast({ tone: 'info', message: `Đang chạy thử v${version.version} trên 6 kỳ` });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const titleStatus = inUse
    ? { label: 'Đã chứng nhận', tone: 'success' as const }
    : { label: 'Chưa chứng nhận', tone: 'warning' as const };
  const usage = metric.usage;

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Chỉ số', metric.code]}
        title={
          <span className="flex items-center gap-[10px]">
            {shown ? `${shown.display_name} · ${metric.code}` : metric.code}
            <StatusDot tone={titleStatus.tone}>{titleStatus.label}</StatusDot>
          </span>
        }
        description={[
          inUse
            ? `v${inUse.version} đang dùng · chứng nhận bởi ${inUse.certified_by_name ?? '—'} ${inUse.certified_at ? formatDate(inUse.certified_at).slice(0, 5) : ''}`
            : 'Chưa có phiên bản được chứng nhận',
          `dùng ở ${usage.artifacts} artifact, ${usage.agents} agent, ${usage.reports} báo cáo định kỳ`,
        ].join(' · ')}
        actions={
          <>
            <Tooltip content="Lịch sử thay đổi chi tiết đang được làm; các phiên bản ở bên phải.">
              <Button kind="ghost" disabled>
                Lịch sử
              </Button>
            </Tooltip>
            <Button loading={busy === 'draft'} disabled={busy !== null} onClick={edit}>
              {draft ? `Sửa → bản nháp v${draft.version}` : 'Sửa → tạo bản nháp'}
            </Button>
          </>
        }
      />
      {error && (
        <div className="px-da-3xl pt-da-lg">
          <Notice tone="error">Không thực hiện được: {error}</Notice>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col pl-da-3xl pr-da-2xl">
          {shown && (
            <Definition
              version={shown}
              onPreview={shown.status === 'draft' ? () => preview(shown) : undefined}
              busy={busy === 'preview'}
            />
          )}
          <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
            <h2 className="text-foreground da-heading-3">Người phụ trách và chứng nhận</h2>
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="w-[160px] px-da-lg py-da-md font-normal">Vai trò</th>
                  <th className="px-da-lg py-da-md font-normal">Người</th>
                  <th className="w-[300px] px-da-lg py-da-md font-normal">Ghi chú</th>
                </tr>
              </thead>
              <tbody>
                {metric.stewardship.map((row) => (
                  <tr key={row.role} className="border-b border-border da-body-small">
                    <td className="px-da-lg py-da-md text-muted-foreground">{row.role}</td>
                    <td className="px-da-lg py-da-md text-foreground da-body-strong">{row.who}</td>
                    <td className="px-da-lg py-da-md text-foreground">{row.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
        <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
          <h2 className="text-foreground da-heading-3">Phiên bản</h2>
          <ul className="flex flex-col">
            {metric.versions.map((v) => {
              const look = VERSION_STATUS[v.status];
              return (
                <li key={v.id}>
                  <button
                    type="button"
                    aria-current={v.id === shown?.id ? 'true' : undefined}
                    onClick={() => setShownId(v.id)}
                    className={cn(
                      'flex w-full items-center gap-[10px] border-b border-border py-[6px] text-left hover:bg-subtle',
                      v.id === shown?.id && 'bg-subtle',
                    )}
                  >
                    <span className="w-[30px] shrink-0 text-foreground da-mono">v{v.version}</span>
                    <span className="min-w-0 flex-1 text-foreground da-body-small">
                      {[v.change_note, v.status === 'draft' ? 'nháp' : monthYear(v.created_at)]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                    <StatusDot tone={look.tone}>{look.label}</StatusDot>
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="text-foreground da-body-small">
            Sửa chỉ số đã chứng nhận luôn tạo bản nháp mới; bản nháp chạy thử trên 6 kỳ và cần chủ
            sở hữu chứng nhận trước khi thay bản đang dùng.
          </p>
        </aside>
      </div>
    </div>
  );
}

function Definition({
  version,
  onPreview,
  busy,
}: {
  version: MetricVersion;
  onPreview?: () => void;
  busy: boolean;
}) {
  const fields: [string, string][] = [
    ['Tên hiển thị', version.display_name],
    ['Đơn vị', `${version.unit ?? '—'}, ${version.decimals} chữ số thập phân`],
    ['Chiều tốt', DIRECTION[version.direction]],
  ];
  return (
    <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
      <div className="flex items-center gap-da-md">
        <h2 className="min-w-0 flex-1 text-foreground da-heading-3">
          Định nghĩa{version.status !== 'certified' ? ` · v${version.version}` : ''}
        </h2>
        {onPreview && (
          <Button size="sm" loading={busy} onClick={onPreview}>
            Chạy thử trên 6 kỳ
          </Button>
        )}
      </div>
      <dl className="flex gap-da-lg">
        {fields.map(([label, value]) => (
          <Field key={label} label={label} value={value} />
        ))}
      </dl>
      <div className="flex flex-col gap-da-xs">
        <p className="text-muted-foreground da-caption">Công thức</p>
        <pre className="whitespace-pre-wrap rounded-sm border border-border bg-background px-da-lg py-[10px] text-foreground da-mono">
          {version.expression}
        </pre>
      </div>
      <dl className="flex gap-da-lg">
        <Field
          label="Kỳ"
          value={`${PERIOD[version.period_semantics]}, ${GRAIN[version.time_grain]}`}
        />
        <Field label="Chiều hợp lệ" value={version.dimension_labels.join(', ') || '—'} />
      </dl>
    </section>
  );
}

/** A read-only value in the field frame of the design (label above, bordered box). */
function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
      <dt className="text-muted-foreground da-caption">{label}</dt>
      <dd className="truncate rounded-sm border border-border-strong px-[10px] py-[7px] text-foreground da-body-small">
        {value}
      </dd>
    </div>
  );
}
