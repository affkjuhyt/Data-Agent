import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Notice } from '../../design-system/Notice';
import { Radio } from '../../design-system/Radio';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import {
  createCrawlSource,
  inferCrawlSchema,
  suggestCrawlSources,
  type ReviewGate,
  type SourceSuggestion,
} from '../../lib/v1/crawl';

/*
 * "P14 · Nguồn thu thập · Đăng ký" (2389:14708, UC-14-01): describe the data to collect; the
 * Agent Thu thập proposes sources and checks robots.txt and the terms of each before anything is
 * saved. A source whose terms forbid automated collection cannot be ticked (BR-149). Scope,
 * schedule and rate are set on the right — the rate is capped at one request per second per
 * domain — then the review gate; "Tự động" opens only after 98% sample accuracy over four runs,
 * which a new source cannot have. Saving starts schema inference and opens the source.
 */
const SCHEDULES = [
  { value: '0 8 * * *', label: 'Hằng ngày 08:00' },
  { value: '0 7 * * *', label: 'Hằng ngày 07:00' },
  { value: '0 * * * *', label: 'Mỗi giờ' },
  { value: '0 8 * * 1', label: 'Thứ Hai 08:00' },
];
const DEPTHS = [1, 2, 3].map((d) => ({ value: String(d), label: `${d} liên kết` }));
const TRUST_LEVEL = 0.7;

export default function CrawlRegisterPage() {
  const { navigate } = useAppState();
  const toast = useToast();
  const [goal, setGoal] = useState('');
  const [suggestion, setSuggestion] = useState<SourceSuggestion>();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [schedule, setSchedule] = useState(SCHEDULES[0].value);
  const [depth, setDepth] = useState('3');
  const [gate, setGate] = useState<ReviewGate>('exceptions');
  const [busy, setBusy] = useState<'suggest' | 'save' | null>(null);
  const [error, setError] = useState<string>();

  const suggest = async () => {
    setBusy('suggest');
    setError(undefined);
    try {
      const result = await suggestCrawlSources(goal.trim());
      setSuggestion(result);
      // Every allowed source starts ticked; forbidden ones cannot be.
      setPicked(
        new Set([
          ...result.sources.filter((s) => s.robots === 'allowed').map((s) => s.key),
          ...(result.more ? ['more'] : []),
        ]),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const chosen = suggestion?.sources.filter((s) => picked.has(s.key)) ?? [];
  const more = suggestion?.more && picked.has('more') ? suggestion.more : null;
  const domainCount = chosen.length + (more?.allowed ?? 0);
  const forbidden = suggestion?.sources.filter((s) => s.robots === 'forbidden') ?? [];
  const sources = useTableView(suggestion?.sources ?? [], {
    search: (s) => `${s.label} ${s.seed_url} ${s.format_label}`,
    filters: [
      {
        id: 'robots',
        label: 'Điều khoản',
        value: (s) => (s.robots === 'allowed' ? 'Cho phép' : 'Cấm thu tự động'),
      },
    ],
    sorts: [
      { id: 'label', value: (s) => s.label },
      { id: 'url', value: (s) => s.seed_url },
      { id: 'format', value: (s) => s.format_label },
      { id: 'robots', value: (s) => (s.robots === 'allowed' ? 'Cho phép' : 'Cấm thu tự động') },
    ],
    pageSize: 50,
  });

  const save = async () => {
    if (!suggestion || domainCount === 0) {
      setError('Tìm nguồn và chọn ít nhất một nguồn trước khi lưu.');
      return;
    }
    setBusy('save');
    setError(undefined);
    try {
      const { id } = await createCrawlSource({
        name: suggestion.suggested_name,
        goal: goal.trim(),
        kind: 'web_listing',
        seed_urls: [...chosen.map((s) => s.seed_url), ...(more?.seed_urls ?? [])],
        allowed_domains: [...chosen.map((s) => s.domain), ...(more?.domains ?? [])],
        max_depth: Number(depth),
        schedule_cron: schedule,
        rate_limit_rps: 1,
        review_gate: gate,
        trust_level: TRUST_LEVEL,
      });
      await inferCrawlSchema(id);
      toast({ tone: 'success', message: 'Đã lưu nguồn; Agent Thu thập đang suy ngược schema' });
      navigate('data', 'CRAWL_SOURCE', id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const toggle = (key: string, on: boolean) => {
    const next = new Set(picked);
    if (on) next.add(key);
    else next.delete(key);
    setPicked(next);
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Thu thập ngoài', 'Thêm nguồn']}
        title="Thêm nguồn thu thập"
        description="Mô tả dữ liệu cần thu; Agent Thu thập đề xuất nguồn, kiểm robots.txt và điều khoản trước khi lưu"
        actions={
          <>
            <Button onClick={() => navigate('data', 'CRAWL')}>Huỷ</Button>
            <Button
              kind="primary"
              loading={busy === 'save'}
              disabled={busy !== null}
              onClick={save}
            >
              Lưu và suy ngược schema
            </Button>
          </>
        }
      />
      <div className="flex items-start gap-da-xl px-da-3xl pb-da-2xl pt-da-xl max-[1279px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col gap-da-xl max-[1279px]:w-full">
          {error && <Notice tone="error">{error}</Notice>}
          <Card title="1 · Mục tiêu thu thập">
            <textarea
              aria-label="Mục tiêu thu thập"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              rows={3}
              placeholder="Ví dụ: thu lãi suất huy động các kỳ hạn của các ngân hàng thương mại, hằng ngày…"
              className="rounded-sm border border-border-strong bg-card px-da-lg py-[10px] text-foreground da-body-small"
            />
            <div>
              <Button
                loading={busy === 'suggest'}
                disabled={!goal.trim() || busy !== null}
                onClick={suggest}
              >
                Tìm nguồn
              </Button>
            </div>
          </Card>
          {suggestion && (
            <Card
              title={`2 · Nguồn agent đề xuất · ${suggestion.sources.length + (suggestion.more?.count ?? 0)} ngân hàng`}
            >
              <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border">
                <thead>
                  <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
                    <th className="w-[200px] px-da-lg py-da-md font-normal">Ngân hàng</th>
                    <th className="px-da-lg py-da-md font-normal">URL hạt giống</th>
                    <th className="w-[120px] px-da-lg py-da-md font-normal">Định dạng</th>
                    <th className="w-[170px] px-da-lg py-da-md font-normal">
                      robots.txt · điều khoản
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {suggestion.sources.map((s) => (
                    <tr key={s.key} className="border-b border-border da-body-small">
                      <td className="px-da-lg py-[10px]">
                        <Checkbox
                          checked={picked.has(s.key)}
                          disabled={s.robots === 'forbidden'}
                          onChange={(on) => toggle(s.key, on)}
                        >
                          {s.label}
                        </Checkbox>
                      </td>
                      <td className="truncate px-da-lg py-[10px] text-foreground">{s.seed_url}</td>
                      <td className="px-da-lg py-[10px] text-foreground">{s.format_label}</td>
                      <td className="px-da-lg py-[10px]">
                        <StatusDot tone={s.robots === 'allowed' ? 'success' : 'error'}>
                          {s.robots === 'allowed' ? 'Cho phép' : 'Cấm thu tự động'}
                        </StatusDot>
                      </td>
                    </tr>
                  ))}
                  {suggestion.more && (
                    <tr className="da-body-small">
                      <td className="px-da-lg py-[10px]">
                        <Checkbox
                          checked={picked.has('more')}
                          onChange={(on) => toggle('more', on)}
                        >
                          {suggestion.more.count} ngân hàng khác
                        </Checkbox>
                      </td>
                      <td className="px-da-lg py-[10px] text-muted-foreground">…</td>
                      <td className="px-da-lg py-[10px]" />
                      <td className="px-da-lg py-[10px] text-muted-foreground">
                        {suggestion.more.allowed} cho phép
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              {forbidden.map((s) => (
                <p key={s.key} className="text-error da-caption">
                  {s.label} cấm thu thập tự động trong điều khoản: không thêm, gợi ý xin dữ liệu qua
                  API hoặc tệp (BR-149).
                </p>
              ))}
            </Card>
          )}
        </div>
        <div className="flex w-[380px] shrink-0 flex-col gap-da-xl max-[1279px]:w-full">
          <Card title="3 · Phạm vi, lịch, tốc độ">
            <dl className="flex flex-col gap-da-lg da-body-small">
              <Row label="Miền cho phép">
                {suggestion ? `${domainCount} miền của ngân hàng đã chọn` : 'Theo nguồn đã chọn'}
              </Row>
              <Row label="Độ sâu tối đa">
                <Select label="Độ sâu tối đa" value={depth} onChange={setDepth} options={DEPTHS} />
              </Row>
              <Row label="Lịch">
                <Select label="Lịch" value={schedule} onChange={setSchedule} options={SCHEDULES} />
              </Row>
              <Row label="Tốc độ">1 yêu cầu/giây/miền</Row>
              <Row label="Xác thực">Không; API key lưu OpenBao khi cần</Row>
              {suggestion && (
                <Row label="Render JavaScript">Chỉ {suggestion.render_js_domains} miền cần</Row>
              )}
            </dl>
          </Card>
          <Card title="4 · Cổng duyệt và độ tin cậy nguồn">
            <div role="radiogroup" aria-label="Cổng duyệt" className="flex flex-col gap-da-md">
              <Radio
                name="gate"
                value="all"
                checked={gate === 'all'}
                onChange={() => setGate('all')}
              >
                Duyệt hết
              </Radio>
              <Radio
                name="gate"
                value="exceptions"
                checked={gate === 'exceptions'}
                onChange={() => setGate('exceptions')}
              >
                Duyệt ngoại lệ (mặc định)
              </Radio>
              <Radio name="gate" value="auto" checked={false} onChange={() => {}} disabled>
                Tự động · bật khi chính xác ≥ 98% trong 4 lượt
              </Radio>
            </div>
            <dl className="da-body-small">
              <Row label="Mức tin cậy nguồn">0,7 · nguồn ngoài</Row>
            </dl>
            <p className="text-muted-foreground da-caption">
              Dữ liệu mang nhãn “nguồn ngoài”; không dùng làm số báo cáo NHNN khi chưa chứng nhận
              (BR-156).
            </p>
          </Card>
          <Card title="5 · Duyệt pháp lý">
            <dl className="flex flex-col gap-da-lg da-body-small">
              <Row label="Điều khoản">Chụp và băm khi lưu</Row>
              <Row label="Trạng thái">Chờ Pháp chế (AC06) duyệt sau khi lưu</Row>
              <Row label="Xem lại">6 tháng hoặc khi điều khoản đổi</Row>
            </dl>
            <p className="text-muted-foreground da-caption">
              Nguồn chỉ chạy khi đã duyệt pháp lý và còn hạn; bằng chứng lưu kèm ảnh chụp điều
              khoản.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-da-lg rounded-md border border-border bg-card p-da-xl">
      <h2 className="text-foreground da-heading-3">{title}</h2>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-da-md">
      <dt className="w-[130px] shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 flex-1 text-foreground">{children}</dd>
    </div>
  );
}
