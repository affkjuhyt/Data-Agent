import { useEffect, useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatNumber } from '../../lib/formatVN';
import {
  backtestPolicy,
  getAccessPolicy,
  listAdminUsers,
  simulatePolicy,
  updateAccessPolicy,
  type AccessPolicy,
  type Backtest,
} from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { POLICY_KIND, POLICY_STATUS } from './policyLabels';

/*
 * "P11 · Chính sách dữ liệu" (2305:19617, UC-11-02): the rule in mono lines, what one person
 * would see with and without it for a sample question (POST :simulate), and on the right the
 * backtest on the last 7 days of queries (POST :backtest). "Lịch sử" waits for a version-history
 * endpoint, so it is shown disabled with the reason.
 */
const HISTORY_PENDING = 'Lịch sử phiên bản chưa có API.';

export default function PolicyDetail({ id }: { id: string }) {
  const toast = useToast();
  const policy = useV1(() => getAccessPolicy(id), [id]);
  // null = unedited: the textarea shows the saved rule.
  const [rule, setRule] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [backtest, setBacktest] = useState<Backtest | null>(null);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!policy.data) return;
    setRule(null);
    setBacktest(policy.data.last_backtest);
  }, [policy.data]);

  const save = async (p: AccessPolicy) => {
    setSaving(true);
    setError(undefined);
    try {
      await updateAccessPolicy(p.id, { rule_text: rule ?? p.rule_text });
      toast({ tone: 'success', message: `Đã lưu ${p.code} thành phiên bản mới` });
      policy.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const rerun = async (p: AccessPolicy) => {
    setRunning(true);
    setError(undefined);
    try {
      const result = await backtestPolicy(p.id);
      if (result) setBacktest(result);
      else toast({ tone: 'success', message: 'Đang chạy thử lại; kết quả hiện khi xong' });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <AsyncRegion state={policy} what="chính sách" skeletonRows={5}>
        {(p) => (
          <>
            <PageHeader
              bordered
              breadcrumb={['Quản trị', 'Chính sách dữ liệu', p.code]}
              title={
                <span className="flex items-center gap-[10px]">
                  {p.name}
                  <StatusDot tone={POLICY_STATUS[p.status].tone}>{POLICY_STATUS[p.status].label}</StatusDot>
                </span>
              }
              description={`${POLICY_KIND[p.kind]} · Data Space ${p.data_space_name} · ${POLICY_STATUS[p.status].label.toLowerCase()} · chủ sở hữu ${p.owner_name}`}
              actions={
                <>
                  <Tooltip content={HISTORY_PENDING}>
                    <Button kind="ghost" disabled>
                      Lịch sử
                    </Button>
                  </Tooltip>
                  <Button loading={running} onClick={() => rerun(p)}>
                    Chạy thử lại
                  </Button>
                  <Button
                    kind="primary"
                    loading={saving}
                    disabled={rule === null || rule === p.rule_text}
                    onClick={() => save(p)}
                  >
                    Lưu thay đổi
                  </Button>
                </>
              }
            />
            {error && (
              <div className="px-da-3xl pt-da-xl">
                <Notice tone="error">{error}</Notice>
              </div>
            )}
            <div className="flex min-h-0 flex-1">
              <div className="flex min-w-0 flex-1 flex-col gap-[14px] pl-da-3xl pr-da-2xl pt-da-xl pb-da-xl">
                <h2 className="text-foreground da-heading-3">Quy tắc</h2>
                <Textarea
                  aria-label="Quy tắc"
                  value={rule ?? p.rule_text}
                  onChange={(e) => setRule(e.target.value)}
                  rows={Math.max(4, (rule ?? p.rule_text).split('\n').length)}
                  spellCheck={false}
                  className="w-full"
                  mono
                />
                <h2 className="text-foreground da-heading-3">Mô phỏng: một người sẽ thấy gì</h2>
                <Simulation policy={p} />
              </div>
              <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
                <h2 className="text-foreground da-heading-3">
                  Chạy thử trên {backtest?.window_days ?? 7} ngày truy vấn
                </h2>
                {backtest ? (
                  <dl className="flex flex-col gap-[10px]">
                    {[
                      [
                        'Truy vấn bị ảnh hưởng',
                        `${formatNumber(backtest.affected_queries)} / ${formatNumber(backtest.total_queries)}`,
                      ],
                      ['Người bị ảnh hưởng', backtest.affected_users_text],
                      ['Truy vấn sẽ trả rỗng', backtest.empty_queries_text],
                    ].map(([label, value]) => (
                      <div key={label} className="flex flex-col gap-[2px] pb-da-sm">
                        <dt className="text-muted-foreground da-caption">{label}</dt>
                        <dd className="text-foreground da-body-small">{value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="text-muted-foreground da-body-small">
                    Chưa chạy thử. Bấm “Chạy thử lại” để xem ảnh hưởng trên truy vấn gần đây.
                  </p>
                )}
                <p className="text-foreground da-body-small">
                  Chính sách áp ở tầng truy vấn: agent và mọi kênh (MCP, API, Teams) đều chịu cùng chính
                  sách.
                </p>
                <p className="text-muted-foreground da-caption">UC-11-02</p>
              </aside>
            </div>
          </>
        )}
      </AsyncRegion>
    </div>
  );
}

function Simulation({ policy }: { policy: AccessPolicy }) {
  const users = useV1(listAdminUsers, []);
  const people = (users.data?.items ?? []).filter((u) => u.kind === 'person');
  // Start with someone the policy narrows, since that is the case worth checking.
  const [userId, setUserId] = useState<string | null>(null);
  const chosen = userId ?? people.find((u) => u.row_scope_limited)?.id ?? people[0]?.id ?? null;
  const question = policy.sample_question || 'dư nợ theo chi nhánh tháng này';
  const result = useV1(
    chosen ? () => simulatePolicy(policy.id, { user_id: chosen, question }) : null,
    [policy.id, chosen, question],
  );

  return (
    <>
      <div className="flex items-center gap-da-md">
        <Select
          label="Người được mô phỏng"
          className="w-[300px]"
          options={people.slice(0, 50).map((u) => ({ value: u.id, label: `${u.display_name} · ${u.group_label}` }))}
          value={chosen}
          onChange={setUserId}
        />
        <span className="text-muted-foreground da-body-small">hỏi “{question}”</span>
      </div>
      <AsyncRegion state={result} what="kết quả mô phỏng" skeletonRows={4}>
        {(r) => <SimulationRows rows={r.rows} />}
      </AsyncRegion>
    </>
  );
}

function SimulationRows({ rows }: { rows: { label: string; without: string; with: string }[] }) {
  const view = useTableView(rows, {
    search: (row) => `${row.label} ${row.without} ${row.with}`,
    filters: [{ id: 'label', label: 'Kết quả', value: (row) => row.label }],
    sorts: [
      { id: 'label', value: (row) => row.label },
      { id: 'without', value: (row) => row.without },
      { id: 'with', value: (row) => row.with },
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-md">
      <TableToolbar view={view} placeholder="Tìm kết quả…" searchLabel="Tìm kết quả mô phỏng" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="label" className="px-da-lg py-da-md font-normal">
              Kết quả
            </SortableTh>
            <SortableTh view={view} column="without" className="w-[160px] px-da-lg py-da-md font-normal">
              Không có chính sách
            </SortableTh>
            <SortableTh view={view} column="with" className="w-[160px] px-da-lg py-da-md font-normal">
              Với chính sách
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((row) => (
            <tr key={row.label} className="border-b border-border">
              <td className="px-da-lg py-da-md text-foreground da-body-small">{row.label}</td>
              <td className="px-da-lg py-da-md text-muted-foreground da-mono">{row.without}</td>
              <td className="px-da-lg py-da-md text-foreground da-mono">{row.with}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </div>
  );
}
