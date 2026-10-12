import { useEffect, useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { exportAudit, searchAudit, type AuditChannel, type AuditEntry, type AuditQuery } from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { isoDay } from './format';

/*
 * "P11 · Kiểm toán" (2306:19586, UC-11-03): the append-only audit log. Search plus three
 * filters (kind of actor, channel, day), then time · who · what · data · channel; a blocked
 * action is drawn in warning. "Xuất cho kiểm toán" asks for a signed CSV (POST /v1/audit:export).
 */
const KINDS: { value: 'all' | AuditEntry['actor_kind']; label: string }[] = [
  { value: 'all', label: 'Loại: tất cả' },
  { value: 'person', label: 'Loại: người' },
  { value: 'agent', label: 'Loại: agent' },
  { value: 'client', label: 'Loại: client tích hợp' },
];

const CHANNELS: { value: 'all' | AuditChannel; label: string }[] = [
  { value: 'all', label: 'Kênh: tất cả' },
  { value: 'web', label: 'Kênh: Web' },
  { value: 'agent', label: 'Kênh: Agent' },
  { value: 'teams', label: 'Kênh: Teams' },
  { value: 'mcp', label: 'Kênh: MCP' },
  { value: 'api', label: 'Kênh: API' },
];

const CHANNEL_LABEL: Record<AuditChannel, string> = {
  web: 'Web',
  agent: 'Agent',
  teams: 'Teams',
  mcp: 'MCP',
  api: 'API',
};

const SEARCH_DELAY_MS = 300;

export default function AuditPage() {
  const toast = useToast();
  const [text, setText] = useState('');
  const [q, setQ] = useState('');
  const [kind, setKind] = useState<'all' | AuditEntry['actor_kind']>('all');
  const [channel, setChannel] = useState<'all' | AuditChannel>('all');
  const [date, setDate] = useState(isoDay(new Date()));
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setQ(text.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const query: AuditQuery = {
    q: q || undefined,
    actor_kind: kind === 'all' ? undefined : kind,
    channel: channel === 'all' ? undefined : channel,
    date: date || undefined,
  };
  const log = useV1(() => searchAudit(query), [q, kind, channel, date]);

  const runExport = async () => {
    setExporting(true);
    try {
      const res = await exportAudit(query);
      toast({
        tone: 'success',
        message: res.needs_approval
          ? 'Bản xuất quá 100.000 dòng, đã gửi đi duyệt trước khi tạo tệp.'
          : 'Đang chuẩn bị tệp CSV có chữ ký. Bạn sẽ nhận liên kết khi xong.',
      });
    } catch (e) {
      toast({ tone: 'error', message: `Không xuất được: ${(e as Error).message}` });
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Kiểm toán']}
        title="Nhật ký kiểm toán"
        description="Chỉ đọc · không ai sửa hay xoá được · giữ 7 năm · mỗi dòng ký số nối tiếp dòng trước"
        actions={
          <Button loading={exporting} onClick={runExport}>
            Xuất cho kiểm toán
          </Button>
        }
      />
      <div className="flex flex-wrap items-center gap-da-md px-da-3xl pt-da-lg">
        <Input
          icon="search"
          aria-label="Tìm trong nhật ký"
          placeholder="Ai, dữ liệu nào, agent nào…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="w-[360px]"
        />
        <Select<'all' | AuditEntry['actor_kind']>
          label="Loại"
          className="w-[180px]"
          options={KINDS}
          value={kind}
          onChange={setKind}
        />
        <Select<'all' | AuditChannel>
          label="Kênh"
          className="w-[150px]"
          options={CHANNELS}
          value={channel}
          onChange={setChannel}
        />
        <Input
          type="date"
          aria-label="Ngày"
          value={date}
          max={isoDay(new Date())}
          onChange={(e) => setDate(e.target.value)}
          className="w-[160px]"
        />
      </div>
      <div className="flex flex-col gap-da-lg px-da-3xl pt-da-lg pb-da-xl">
        <AsyncRegion
          state={log}
          what="nhật ký kiểm toán"
          skeletonRows={6}
          isEmpty={(p) => p.items.length === 0}
          empty={
            <EmptyState
              icon="file"
              title="Không có sự kiện nào khớp"
              description="Thử bỏ bớt bộ lọc hoặc chọn ngày khác."
            />
          }
        >
          {(p) => (
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="w-[114px] px-da-lg py-da-md font-normal">Thời gian</th>
                  <th className="w-[202px] px-da-lg py-da-md font-normal">Ai</th>
                  <th className="px-da-lg py-da-md font-normal">Làm gì</th>
                  <th className="w-[232px] px-da-lg py-da-md font-normal">Dữ liệu</th>
                  <th className="w-[102px] px-da-lg py-da-md font-normal">Kênh</th>
                </tr>
              </thead>
              <tbody>
                {p.items.map((e) => (
                  <tr key={e.id} className="border-b border-border align-middle">
                    <td className="px-da-lg py-da-md text-muted-foreground da-mono">{formatTime(e.at, true)}</td>
                    <td className="px-da-lg py-da-md text-foreground da-body-small">{e.actor_label}</td>
                    <td
                      className={cn(
                        'px-da-lg py-da-md da-body-small',
                        e.outcome === 'blocked' ? 'text-warning' : 'text-foreground',
                      )}
                    >
                      {e.action_text}
                    </td>
                    <td className="px-da-lg py-da-md text-muted-foreground da-mono">{e.data_text}</td>
                    <td className="px-da-lg py-da-md text-muted-foreground da-body-small">{CHANNEL_LABEL[e.channel]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </AsyncRegion>
        <p className="text-muted-foreground da-caption">
          UC-11-03 · kiểm toán viên chỉ xem được nhật ký, không xem dữ liệu nghiệp vụ; câu hỏi chứa dữ
          liệu cá nhân được lưu dạng đã che
        </p>
      </div>
    </div>
  );
}
