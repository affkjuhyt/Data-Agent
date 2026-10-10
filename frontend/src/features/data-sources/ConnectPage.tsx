import { Fragment, useMemo, useState, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Radio } from '../../design-system/Radio';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { formatNumber, formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { listDataSpaces, listUsers } from '../../lib/v1/api';
import {
  activateSource,
  createSource,
  listConnectors,
  listSecretRefs,
  testSource,
  updateSource,
  type ActivateSource,
  type ConnectionTest,
  type Connector,
  type NewSource,
} from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { ConnectorPicker } from './ConnectorPicker';

/*
 * "Kết nối nguồn" (UC-04-01, UC-04-04, UC-04-05). /data/connect is step 1, the connector
 * catalogue; /data/connect/<connector> runs steps 2–5:
 *   2 "Thông tin & chế độ" (2279:16775) — the form, a read-only account, the password only as
 *     a secret reference, and federated vs ingest;
 *   3 "Kiểm tra" (2279:16968) — five checks; a write grant blocks the flow (the account must
 *     not be able to change the source) and shows the REVOKE that fixes it;
 *   4 "Chọn dữ liệu" — no frame in Figma: a checklist of the tables the test found;
 *   5 "Data Space & lịch" (2280:16726) — each table's Data Space and owner, scan and
 *     profiling schedules, and what happens after saving.
 */
type Step = 2 | 3 | 4 | 5;

const STEPS = ['Chọn nguồn', 'Kết nối', 'Kiểm tra', 'Chọn dữ liệu', 'Data Space & lịch'];

export default function ConnectPage() {
  const { routeId } = useAppState();
  const connectors = useV1(routeId ? listConnectors : null, [routeId]);
  if (!routeId) return <ConnectorPicker />;
  return (
    <AsyncRegion state={connectors} what="connector">
      {(page) => {
        const connector = page.items.find((c) => c.code === routeId);
        if (!connector?.form)
          return (
            <div className="px-da-3xl py-da-xl">
              <Notice tone="error">Connector “{routeId}” chưa có màn thiết lập.</Notice>
            </div>
          );
        return <ConnectFlow connector={connector} />;
      }}
    </AsyncRegion>
  );
}

function fill(template: string, values: Record<string, string | number>) {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => String(values[key] ?? `{${key}}`));
}

function ConnectFlow({ connector }: { connector: Connector }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const form0 = connector.form!;
  const [step, setStep] = useState<Step>(2);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [test, setTest] = useState<ConnectionTest | null>(null);
  const [form, setForm] = useState({
    name: '',
    host: '',
    port: String(form0.default_port),
    database: '',
    username: '',
    secret_ref: '',
    mode: 'ingest' as NewSource['mode'],
  });
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [assign, setAssign] = useState<Record<string, { data_space_id: string; owner_id: string }>>(
    {},
  );
  const [schedule, setSchedule] = useState({
    schema_scan: 'daily_0200',
    profiling: 'weekly_sun_0300',
    on_schema_change: 'pause_and_ask' as ActivateSource['on_schema_change'],
  });

  const set = (key: keyof typeof form) => (value: string) =>
    setForm((f) => ({ ...f, [key]: value }));
  const body = (): NewSource => ({
    name: form.name.trim(),
    connector: connector.code,
    mode: form.mode,
    config: {
      host: form.host.trim(),
      port: Number(form.port),
      database: form.database.trim(),
      username: form.username.trim(),
      secret_ref: form.secret_ref,
    },
  });
  const formReady =
    form.name.trim() &&
    form.host.trim() &&
    form.username.trim() &&
    form.secret_ref &&
    Number(form.port) > 0;

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const runTest = (id: string) =>
    run(async () => {
      setTest(await testSource(id));
      setStep(3);
    });

  const toTest = () =>
    run(async () => {
      const id = sourceId ?? (await createSource(body())).id;
      if (sourceId) await updateSource(sourceId, body());
      setSourceId(id);
      setTest(await testSource(id));
      setStep(3);
    });

  const save = () =>
    run(async () => {
      if (!sourceId || !test) return;
      const tables = test.tables
        .filter((t) => chosen.has(t.name))
        .map((t) => ({ name: t.name, ...assign[t.name] }));
      await activateSource(sourceId, { tables, ...schedule });
      toast({ tone: 'success', message: `Đã lưu ${form.name}. Đang quét cấu trúc.` });
      navigate('data', 'SOURCE', sourceId);
    });

  const passed = test?.checks.every((c) => c.ok) ?? false;
  const actions: Record<Step, ReactNode> = {
    2: (
      <>
        <Button kind="ghost" onClick={() => navigate('data', 'SOURCES')}>
          Huỷ
        </Button>
        <Button kind="primary" loading={busy} disabled={!formReady} onClick={() => void toTest()}>
          Tiếp: kiểm tra
        </Button>
      </>
    ),
    3: (
      <>
        <Button kind="ghost" onClick={() => setStep(2)}>
          Quay lại
        </Button>
        <Button loading={busy} onClick={() => sourceId && void runTest(sourceId)}>
          Kiểm tra lại
        </Button>
        <Button kind="primary" disabled={!passed} onClick={() => setStep(4)}>
          Tiếp: chọn dữ liệu
        </Button>
      </>
    ),
    4: (
      <>
        <Button kind="ghost" onClick={() => setStep(3)}>
          Quay lại
        </Button>
        <Button kind="primary" disabled={chosen.size === 0} onClick={() => setStep(5)}>
          Tiếp: Data Space & lịch
        </Button>
      </>
    ),
    5: (
      <>
        <Button kind="ghost" onClick={() => setStep(4)}>
          Quay lại
        </Button>
        <Button
          kind="primary"
          loading={busy}
          disabled={[...chosen].some((t) => !assign[t]?.data_space_id || !assign[t]?.owner_id)}
          onClick={() => void save()}
        >
          Lưu và bắt đầu
        </Button>
      </>
    ),
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Nguồn', 'Kết nối mới']}
        title={`Kết nối ${connector.name}`}
        actions={actions[step]}
      />
      <StepBar current={step} />
      {error && (
        <div className="px-da-3xl pt-da-lg">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      {step === 2 && <ConnectionForm connector={connector} form={form} set={set} />}
      {step === 3 && test && <TestResult test={test} />}
      {step === 4 && test && (
        <ChooseTables
          test={test}
          chosen={chosen}
          onToggle={(name, on) =>
            setChosen((s) => {
              const next = new Set(s);
              if (on) next.add(name);
              else next.delete(name);
              return next;
            })
          }
        />
      )}
      {step === 5 && test && (
        <Assign
          test={test}
          mode={form.mode}
          chosen={chosen}
          assign={assign}
          setAssign={setAssign}
          schedule={schedule}
          setSchedule={setSchedule}
        />
      )}
    </div>
  );
}

/** "Bước": done steps show a check in an outlined circle, the current one is filled. */
function StepBar({ current }: { current: number }) {
  return (
    <ol
      aria-label="Các bước kết nối"
      className="flex flex-wrap items-center border-b border-border px-da-3xl py-da-lg"
    >
      {STEPS.map((label, i) => {
        const n = i + 1;
        const state = n < current ? 'done' : n === current ? 'current' : 'todo';
        return (
          <Fragment key={label}>
            {i > 0 && <li aria-hidden className="mx-da-lg h-px w-10 bg-border" />}
            <li
              aria-current={state === 'current' ? 'step' : undefined}
              className="flex items-center gap-da-md"
            >
              <span
                className={cn(
                  'inline-flex size-5 items-center justify-center rounded-full da-caption',
                  state === 'done' && 'border border-foreground text-foreground',
                  state === 'current' && 'bg-foreground text-card',
                  state === 'todo' && 'border border-border-strong text-muted-foreground',
                )}
              >
                {state === 'done' ? <Icon name="check" size={12} label="Xong" /> : n}
              </span>
              <span
                className={cn(
                  state === 'current' ? 'text-foreground da-body-strong' : 'da-body-small',
                  state === 'done' && 'text-foreground',
                  state === 'todo' && 'text-muted-foreground',
                )}
              >
                {label}
              </span>
            </li>
          </Fragment>
        );
      })}
    </ol>
  );
}

type FormState = {
  name: string;
  host: string;
  port: string;
  database: string;
  username: string;
  secret_ref: string;
  mode: NewSource['mode'];
};

function ConnectionForm({
  connector,
  form,
  set,
}: {
  connector: Connector;
  form: FormState;
  set: (key: keyof FormState) => (value: string) => void;
}) {
  const secrets = useV1(listSecretRefs, []);
  const values = { host: form.host || '…', port: form.port, user: form.username || '…' };
  const modes: { value: NewSource['mode']; title: string; text: string }[] = [
    {
      value: 'federated',
      title: 'Truy vấn tại chỗ',
      text: 'Agent chạy câu lệnh chỉ đọc trên nguồn. Dữ liệu luôn mới, không sao chép; tải lên nguồn tăng khi hỏi nhiều.',
    },
    {
      value: 'ingest',
      title: 'Nạp về',
      text: 'Sao dữ liệu theo lịch hoặc liên tục (CDC). Hỏi nhanh, có lịch sử theo thời điểm; cần thiết lập job nạp.',
    },
  ];
  return (
    <div className="flex flex-1 max-[1023px]:flex-col">
      <div className="flex w-[620px] shrink-0 flex-col gap-da-xl border-r border-border pl-da-3xl pr-da-2xl pt-[20px] max-[1023px]:w-full max-[1023px]:border-r-0 max-[1023px]:pr-da-3xl">
        <Field labelTone="muted" label="Tên hiển thị">
          <Input
            value={form.name}
            onChange={(e) => set('name')(e.target.value)}
            placeholder="Hệ thống thẻ tín dụng"
          />
        </Field>
        <div className="flex gap-da-md">
          <div className="min-w-0 flex-1">
            <Field labelTone="muted" label="Máy chủ">
              <Input
                value={form.host}
                onChange={(e) => set('host')(e.target.value)}
                placeholder="card-db.noibo"
              />
            </Field>
          </div>
          <div className="w-[90px]">
            <Field labelTone="muted" label="Cổng">
              <Input
                value={form.port}
                inputMode="numeric"
                onChange={(e) => set('port')(e.target.value.replace(/\D/g, ''))}
              />
            </Field>
          </div>
          <div className="w-[150px]">
            <Field labelTone="muted" label={connector.form!.database_label}>
              <Input value={form.database} onChange={(e) => set('database')(e.target.value)} />
            </Field>
          </div>
        </div>
        <div className="flex gap-da-md">
          <div className="min-w-0 flex-1">
            <Field
              labelTone="muted"
              label="Tài khoản"
              hint="Tài khoản chỉ đọc. Bước sau sẽ kiểm tra không có quyền ghi."
            >
              <Input
                value={form.username}
                onChange={(e) => set('username')(e.target.value)}
                placeholder="agent_ro"
              />
            </Field>
          </div>
          <div className="min-w-0 flex-1">
            <Field labelTone="muted" label="Mật khẩu" hint="Không lưu mật khẩu trong Data Agent.">
              <Select
                label="Mật khẩu"
                placeholder="Chọn khoá trong kho bí mật"
                value={form.secret_ref || null}
                onChange={set('secret_ref')}
                options={(secrets.data?.items ?? []).map((s) => ({ value: s.ref, label: s.label }))}
              />
            </Field>
          </div>
        </div>
        <fieldset className="flex flex-col gap-da-md">
          <legend className="mb-da-md text-foreground da-label">Chế độ dùng dữ liệu</legend>
          <div className="flex gap-da-md max-[767px]:flex-col">
            {modes.map((m) => (
              <div
                key={m.value}
                onClick={() => set('mode')(m.value)}
                className={cn(
                  'flex min-w-0 flex-1 cursor-pointer flex-col gap-da-xs rounded-md border px-[14px] py-da-lg',
                  form.mode === m.value ? 'border-foreground' : 'border-border',
                )}
              >
                <Radio
                  name="mode"
                  value={m.value}
                  checked={form.mode === m.value}
                  onChange={set('mode')}
                >
                  <span className="da-body-strong">{m.title}</span>
                </Radio>
                <span className="text-muted-foreground da-caption">{m.text}</span>
              </div>
            ))}
          </div>
        </fieldset>
        <p className="text-muted-foreground da-caption">
          UC-04-01 · tài khoản phải chỉ đọc; mật khẩu chỉ lưu trong kho bí mật
        </p>
      </div>
      <aside className="flex min-w-0 flex-1 flex-col gap-[10px] bg-background px-da-3xl pt-[20px] max-[1023px]:pb-da-xl">
        <h2 className="text-foreground da-heading-3">Cần chuẩn bị từ đội quản trị CSDL</h2>
        {connector.prep_notes.map((note) => (
          <p key={note} className="text-foreground da-body-small">
            · {fill(note, values)}
          </p>
        ))}
        {connector.sample_sql && (
          <>
            <h2 className="text-foreground da-heading-3">Câu lệnh mẫu</h2>
            <pre className="whitespace-pre-wrap rounded-sm border border-border bg-card px-da-lg py-[10px] text-foreground da-mono">
              {fill(connector.sample_sql, values)}
            </pre>
          </>
        )}
      </aside>
    </div>
  );
}

function TestResult({ test }: { test: ConnectionTest }) {
  const toast = useToast();
  const ok = test.checks.filter((c) => c.ok).length;
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast({ tone: 'success', message: 'Đã sao chép câu lệnh.' });
    } catch {
      toast({
        tone: 'error',
        message: 'Trình duyệt không cho sao chép; hãy chọn và sao chép tay.',
      });
    }
  };
  return (
    <div className="flex flex-col gap-da-xl px-da-3xl pt-[20px]">
      <div className="flex items-center gap-[10px]">
        <h2 className="text-foreground da-heading-3">Kết quả kiểm tra</h2>
        <span className="text-muted-foreground da-caption">
          {ok}/{test.checks.length} đạt · {formatTime(test.checked_at)}
        </span>
      </div>
      <ul className="overflow-hidden rounded-md border border-border">
        {test.checks.map((check) => (
          <li
            key={check.key}
            className={cn(
              'flex items-center gap-da-lg border-b border-border px-da-xl py-da-lg last:border-b-0',
              !check.ok && 'bg-error-subtle',
            )}
          >
            <Icon
              name={check.ok ? 'check' : 'x'}
              size={14}
              className={check.ok ? 'text-success' : 'text-error'}
              label={check.ok ? 'Đạt' : 'Không đạt'}
            />
            <span className="min-w-0 flex-1 text-foreground da-body-strong">{check.title}</span>
            <span
              className={cn('da-body-small', check.ok ? 'text-muted-foreground' : 'text-error')}
            >
              {check.detail}
            </span>
          </li>
        ))}
      </ul>
      {test.fix && (
        <section className="flex flex-col gap-da-md">
          <h2 className="text-foreground da-heading-3">Vì sao không đi tiếp được</h2>
          <p className="text-foreground da-body-small">{test.fix.reason}</p>
          <pre className="whitespace-pre-wrap rounded-sm border border-border bg-background px-da-lg py-[10px] text-foreground da-mono">
            {test.fix.sql}
          </pre>
          <div>
            <button
              type="button"
              onClick={() => void copy(test.fix!.sql)}
              className="text-primary da-body-strong hover:underline"
            >
              Sao chép câu lệnh
            </button>
          </div>
        </section>
      )}
      <p className="text-muted-foreground da-caption">
        UC-04-01 · ngoại lệ: tài khoản có quyền ghi → chặn, không cho lưu kết nối
      </p>
    </div>
  );
}

function ChooseTables({
  test,
  chosen,
  onToggle,
}: {
  test: ConnectionTest;
  chosen: Set<string>;
  onToggle: (name: string, on: boolean) => void;
}) {
  return (
    <div className="flex flex-col gap-da-lg px-da-3xl pt-[20px]">
      <div className="flex items-center gap-[10px]">
        <h2 className="text-foreground da-heading-3">Chọn bảng sẽ dùng</h2>
        <span className="text-muted-foreground da-caption">
          {test.table_count} bảng trong schema {test.schema} · đã chọn {chosen.size}
        </span>
      </div>
      <ul className="flex flex-col">
        {test.tables.map((table) => (
          <li
            key={table.name}
            className="flex items-center gap-da-lg border-b border-border px-da-lg py-[9px]"
          >
            <Checkbox checked={chosen.has(table.name)} onChange={(on) => onToggle(table.name, on)}>
              <span className="flex flex-col gap-[2px]">
                <span className="text-foreground da-mono">{table.name}</span>
                {table.description && (
                  <span className="text-muted-foreground da-caption">{table.description}</span>
                )}
              </span>
            </Checkbox>
            <span className="flex-1" />
            <span className="text-muted-foreground da-mono">
              ~{formatNumber(table.estimated_rows)} dòng
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const SCAN_OPTIONS = [
  { value: 'daily_0200', label: 'Mỗi ngày 02:00' },
  { value: 'hourly', label: 'Mỗi giờ' },
  { value: 'weekly_sun_0200', label: 'Mỗi tuần, Chủ nhật 02:00' },
];
const PROFILE_OPTIONS = [
  { value: 'weekly_sun_0300', label: 'Mỗi tuần, Chủ nhật 03:00' },
  { value: 'daily_0300', label: 'Mỗi ngày 03:00' },
  { value: 'monthly_1_0300', label: 'Mỗi tháng, ngày 1 03:00' },
];
const CHANGE_OPTIONS: { value: ActivateSource['on_schema_change']; label: string }[] = [
  { value: 'pause_and_ask', label: 'Tạm dừng job nạp và hỏi người phụ trách' },
  { value: 'continue_and_notify', label: 'Nạp tiếp và báo người phụ trách' },
];

function Assign({
  test,
  mode,
  chosen,
  assign,
  setAssign,
  schedule,
  setSchedule,
}: {
  test: ConnectionTest;
  mode: NewSource['mode'];
  chosen: Set<string>;
  assign: Record<string, { data_space_id: string; owner_id: string }>;
  setAssign: (
    fn: (
      a: Record<string, { data_space_id: string; owner_id: string }>,
    ) => Record<string, { data_space_id: string; owner_id: string }>,
  ) => void;
  schedule: Omit<ActivateSource, 'tables'>;
  setSchedule: (fn: (s: Omit<ActivateSource, 'tables'>) => Omit<ActivateSource, 'tables'>) => void;
}) {
  const spaces = useV1(listDataSpaces, []);
  const users = useV1(listUsers, []);
  const people = useMemo(
    () =>
      (users.data?.items ?? [])
        .filter((u) => u.kind === 'person')
        .map((u) => ({ value: u.id, label: u.display_name })),
    [users.data],
  );
  const tables = test.tables.filter((t) => chosen.has(t.name));
  const rows = tables.reduce((sum, t) => sum + t.estimated_rows, 0);
  const pick = (table: string, key: 'data_space_id' | 'owner_id') => (value: string) =>
    setAssign((a) => ({
      ...a,
      [table]: { data_space_id: '', owner_id: '', ...a[table], [key]: value },
    }));

  return (
    <div className="flex flex-1 max-[1023px]:flex-col">
      <div className="flex min-w-0 flex-1 flex-col pl-da-3xl pr-da-2xl">
        <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
          <div className="flex items-center gap-da-md">
            <h2 className="flex-1 text-foreground da-heading-3">Gán bảng vào Data Space</h2>
            <span className="text-muted-foreground da-caption">
              {tables.length} bảng đã chọn ở bước 4
            </span>
          </div>
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="px-da-lg py-da-md font-normal">Bảng</th>
                <th className="w-[200px] px-da-md py-da-md font-normal">Data Space</th>
                <th className="w-[200px] px-da-md py-da-md font-normal">Người phụ trách</th>
                <th className="w-[150px] px-da-md py-da-md font-normal max-[1279px]:hidden">
                  Dữ liệu cá nhân
                </th>
              </tr>
            </thead>
            <tbody>
              {tables.map((t) => (
                <tr key={t.name} className="border-b border-border">
                  <td className="px-da-lg py-[9px]">
                    <div className="flex flex-col gap-[2px]">
                      <span className="text-foreground da-mono">{t.name}</span>
                      {t.description && (
                        <span className="text-muted-foreground da-caption">{t.description}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-da-md py-[9px]">
                    <Select
                      label={`Data Space của ${t.name}`}
                      placeholder="Chọn Data Space"
                      value={assign[t.name]?.data_space_id || null}
                      onChange={pick(t.name, 'data_space_id')}
                      options={(spaces.data?.items ?? []).map((d) => ({
                        value: d.id,
                        label: d.name,
                      }))}
                    />
                  </td>
                  <td className="px-da-md py-[9px]">
                    <Select
                      label={`Người phụ trách ${t.name}`}
                      placeholder="Chọn người"
                      value={assign[t.name]?.owner_id || null}
                      onChange={pick(t.name, 'owner_id')}
                      options={people}
                    />
                  </td>
                  <td
                    className={cn(
                      'px-da-md py-[9px] da-body-small max-[1279px]:hidden',
                      t.personal_data ? 'text-foreground' : 'text-muted-foreground',
                    )}
                  >
                    {t.personal_data ?? 'Không'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-muted-foreground da-caption">
            Người phụ trách duyệt mọi thay đổi cấu trúc và yêu cầu quyền trên bảng của mình. Một
            bảng có thể dùng ở nhiều Data Space; quyền theo dòng, cột đặt ở Quản trị · Chính sách.
          </p>
        </section>
        <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
          <h2 className="text-foreground da-heading-3">Quét cấu trúc và hồ sơ dữ liệu</h2>
          <div className="flex gap-da-lg max-[767px]:flex-col">
            <div className="min-w-0 flex-1">
              <Field
                labelTone="muted"
                label="Quét cấu trúc"
                hint="Phát hiện cột thêm, đổi kiểu, bị xoá"
              >
                <Select
                  label="Quét cấu trúc"
                  value={schedule.schema_scan}
                  onChange={(v) => setSchedule((s) => ({ ...s, schema_scan: v }))}
                  options={SCAN_OPTIONS}
                />
              </Field>
            </div>
            <div className="min-w-0 flex-1">
              <Field
                labelTone="muted"
                label="Lập hồ sơ dữ liệu"
                hint="Thống kê giá trị trên mẫu 1%, đã che dữ liệu cá nhân"
              >
                <Select
                  label="Lập hồ sơ dữ liệu"
                  value={schedule.profiling}
                  onChange={(v) => setSchedule((s) => ({ ...s, profiling: v }))}
                  options={PROFILE_OPTIONS}
                />
              </Field>
            </div>
            <div className="min-w-0 flex-1">
              <Field labelTone="muted" label="Khi cấu trúc thay đổi">
                <Select
                  label="Khi cấu trúc thay đổi"
                  value={schedule.on_schema_change}
                  onChange={(v) => setSchedule((s) => ({ ...s, on_schema_change: v }))}
                  options={CHANGE_OPTIONS}
                />
              </Field>
            </div>
          </div>
        </section>
      </div>
      <aside className="flex w-[400px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1023px]:w-full max-[1023px]:border-l-0 max-[1023px]:border-t max-[1023px]:pb-da-xl">
        <h2 className="text-foreground da-heading-3">Sau khi lưu</h2>
        <After label="Ngay bây giờ">
          Quét cấu trúc {test.table_count} bảng, lập hồ sơ {tables.length} bảng đã chọn (khoảng{' '}
          {test.scan_minutes} phút)
        </After>
        {mode === 'ingest' && (
          <>
            <After label={`Trong ${test.initial_load_hours} giờ tới`}>
              Nạp lần đầu khoảng{' '}
              {rows >= 1_000_000
                ? `${formatNumber(Math.round(rows / 1_000_000))} triệu`
                : formatNumber(rows)}{' '}
              dòng
            </After>
            <After label="Từ lần sau">Chỉ nạp phần thay đổi theo job đã đặt</After>
          </>
        )}
        <After label="Thông báo">Người phụ trách nhận tin khi nạp xong hoặc lỗi</After>
        <p className="text-muted-foreground da-caption">UC-04-04 · UC-04-05</p>
      </aside>
    </div>
  );
}

function After({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-[2px] pb-da-md">
      <span className="text-muted-foreground da-caption">{label}</span>
      <span className="text-foreground da-body-small">{children}</span>
    </div>
  );
}
