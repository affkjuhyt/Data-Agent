import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { Tabs } from '../../design-system/Tabs';
import { V1Error } from '../../lib/v1/client';
import { getVault, listTheses, type Vault } from '../../lib/v1/exec';
import { useV1, type V1State } from '../../lib/v1/useV1';
import type { VaultSession } from '../../lib/vault/session';
import { openTheses, type OpenThesis } from './content';
import { EvidenceTab } from './EvidenceTab';
import { PublishTab } from './PublishTab';
import { ScoresTab } from './ScoresTab';
import { ThesesTab } from './ThesesTab';
import { SetupPanel, UnlockPanel } from './UnlockPanel';

/*
 * "P16 · Lớp riêng lãnh đạo" (section 2416:15013, UC-16-01…06): the executive's own vault, a rail
 * item of Phân tích shown only with the exec_vault role. Four tabs on one page — Luận điểm
 * (2425:15275), Bằng chứng (2425:15639), Chấm điểm (2426:15422), Công bố & khoá (2426:15808).
 *
 * Nothing here is readable without the vault key: the page first unlocks the vault on this device
 * (lib/vault/session), keeps the key in memory only while the page is open, and drops it after 15
 * minutes without input, on "Khoá ngay" and when the page unmounts (G10.1).
 */
export type VaultTab = 'theses' | 'evidence' | 'scores' | 'publish';

const TABS: { value: VaultTab; label: string }[] = [
  { value: 'theses', label: 'Luận điểm' },
  { value: 'evidence', label: 'Bằng chứng' },
  { value: 'scores', label: 'Chấm điểm' },
  { value: 'publish', label: 'Công bố & khoá' },
];

const IDLE_MS = 15 * 60_000;

interface VaultContextValue {
  vault: Vault;
  reloadVault: () => void;
  session: VaultSession;
  theses: V1State<OpenThesis[]>;
  /** The thesis the evidence and publish tabs are about. */
  selected: OpenThesis | undefined;
  select: (id: string) => void;
  goTo: (tab: VaultTab) => void;
}

const VaultContext = createContext<VaultContextValue | null>(null);

export function useVault() {
  const value = useContext(VaultContext);
  if (!value) throw new Error('useVault outside an open vault');
  return value;
}

/** Seconds left before the vault locks itself; resets on any pointer or key input. */
function useIdleLock(active: boolean, lock: () => void) {
  const [left, setLeft] = useState(IDLE_MS);
  const last = useRef(Date.now());
  useEffect(() => {
    if (!active) return;
    last.current = Date.now();
    setLeft(IDLE_MS);
    const touch = () => {
      last.current = Date.now();
    };
    const tick = window.setInterval(() => {
      const remaining = IDLE_MS - (Date.now() - last.current);
      if (remaining <= 0) lock();
      else setLeft(remaining);
    }, 1000);
    window.addEventListener('pointerdown', touch);
    window.addEventListener('keydown', touch);
    return () => {
      window.clearInterval(tick);
      window.removeEventListener('pointerdown', touch);
      window.removeEventListener('keydown', touch);
    };
  }, [active, lock]);
  return left;
}

const mmss = (ms: number) => {
  const s = Math.ceil(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};

export default function VaultPage() {
  const vault = useV1(['getVault'], () => getVault());
  const [session, setSession] = useState<VaultSession | null>(null);
  const [tab, setTab] = useState<VaultTab>('theses');
  const [selectedId, setSelectedId] = useState<string>();
  const sessionRef = useRef<VaultSession | null>(null);
  sessionRef.current = session;

  const lock = useCallback(() => {
    sessionRef.current?.lock();
    setSession(null);
  }, []);
  // Leaving the page closes the vault: the key never outlives the screen that needs it.
  useEffect(() => () => sessionRef.current?.lock(), []);
  const left = useIdleLock(session !== null, lock);

  const theses = useV1(
    ['VaultPage.openTheses', session !== null],
    session ? async () => openTheses(session, (await listTheses()).items) : null,
    { cache: false },
  );
  const items = theses.data ?? [];
  const selected =
    items.find((t) => t.id === selectedId) ?? items.find((t) => t.status === 'open') ?? items[0];

  const banner = (
    <div className="flex flex-wrap items-center gap-[10px] rounded-lg border border-border bg-subtle py-da-md pl-da-lg pr-da-md">
      <Icon name="lock" className="text-foreground" />
      <span className="text-foreground da-body-strong">Kho riêng · chỉ bạn đọc được</span>
      {session ? (
        <>
          <Badge tone="success">Đang mở trên thiết bị này</Badge>
          <span className="text-muted-foreground da-caption">
            {session.unlockedWith.label} · tự khoá sau 15 phút không thao tác
          </span>
          <span className="flex-1" />
          <span className="text-foreground da-mono" aria-live="off">
            Tự khoá sau {mmss(left)}
          </span>
          <Button onClick={lock}>Khoá ngay</Button>
        </>
      ) : (
        <Badge>Đang khoá</Badge>
      )}
    </div>
  );

  const missing = vault.error instanceof V1Error && vault.error.status === 404;
  const frame = (header: HeaderProps, body: ReactNode) => (
    <div className="flex min-h-full flex-col">
      <PageHeader bordered {...header} />
      <div className="flex flex-1 flex-col gap-da-lg px-da-3xl pb-da-2xl pt-da-xl">
        {banner}
        <Tabs<VaultTab> label="Kho riêng" items={TABS} value={tab} onChange={setTab} />
        {body}
      </div>
    </div>
  );

  if (!session || !vault.data) {
    const header = { breadcrumb: ['Kho riêng'], title: 'Kho riêng' };
    if (missing) return frame(header, <SetupPanel onCreated={vault.reload} />);
    return frame(
      header,
      <AsyncRegion state={vault} what="kho riêng">
        {(v) => <UnlockPanel vault={v} onUnlocked={setSession} />}
      </AsyncRegion>,
    );
  }

  const context: VaultContextValue = {
    vault: vault.data,
    reloadVault: vault.reload,
    session,
    theses,
    selected,
    select: setSelectedId,
    goTo: setTab,
  };
  const Body = { theses: ThesesTab, evidence: EvidenceTab, scores: ScoresTab, publish: PublishTab }[
    tab
  ];
  return (
    <VaultContext.Provider value={context}>
      <Body frame={frame} />
    </VaultContext.Provider>
  );
}

export interface HeaderProps {
  title: string;
  breadcrumb: string[];
  description?: ReactNode;
  actions?: ReactNode;
}

/** Each tab draws its own header (title, actions) above the shared banner and tabs. */
export type Frame = (header: HeaderProps, body: ReactNode) => ReactNode;
