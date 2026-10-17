import { useEffect, useState, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { useAuth } from '../../lib/AuthProvider';
import { getMyDataSpaces, getOnboarding, type MyDataSpaces, type Onboarding } from '../../lib/v1/explore';
import { DataSpacePicker } from './DataSpacePicker';
import { OnboardingPage } from './OnboardingPage';

/*
 * "Nhãn · Toàn cục" (2376:14142): Đăng nhập → chọn Data Space → onboarding → hiểu cách agent làm
 * việc → Trang chủ. Both gates are the server's call (GET /v1/data-spaces, GET /v1/me/onboarding),
 * so another browser does not repeat them. The Data Space picker is skipped when the viewer
 * asked to open the last space and one was used. Either call failing (the backend does not
 * serve /v1 yet) opens the app straight away rather than locking it behind a setup screen.
 */
type Stage =
  | { kind: 'loading' }
  | { kind: 'pick'; spaces: MyDataSpaces; onboarding: Onboarding | null }
  | { kind: 'onboarding' }
  | { kind: 'app'; spaceId: string };

export function FirstRunGate({ children }: { children: (spaceId: string) => ReactNode }) {
  const { user, navigate, setDataSpace } = useAppState();
  const { logout } = useAuth();
  const [stage, setStage] = useState<Stage>({ kind: 'loading' });
  const [spaceId, setSpaceId] = useState('');
  const [onboarding, setOnboarding] = useState<Onboarding | null>(null);

  // Onboarding is rendered before AppShell, so it must activate the selected Data Space itself.
  // Otherwise the first-agent request has no X-Data-Space header and its scoped capability is
  // evaluated against the organisation-wide (empty) capability set.
  const activateSpace = (id: string, name?: string) => {
    setSpaceId(id);
    if (id) setDataSpace({ id, name: name ?? id });
  };

  useEffect(() => {
    let alive = true;
    Promise.allSettled([getMyDataSpaces(), getOnboarding()]).then(([spaces, ob]) => {
      if (!alive) return;
      const pending = ob.status === 'fulfilled' && ob.value.state === 'pending' ? ob.value : null;
      setOnboarding(pending);
      if (spaces.status !== 'fulfilled') {
        setStage(pending ? { kind: 'onboarding' } : { kind: 'app', spaceId: '' });
        return;
      }
      const last = spaces.value.items.find((s) => s.access === 'member' && s.last_used_text);
      if (spaces.value.open_last_used && last) {
        activateSpace(last.id, last.name);
        setStage(pending ? { kind: 'onboarding' } : { kind: 'app', spaceId: last.id });
        return;
      }
      setStage({ kind: 'pick', spaces: spaces.value, onboarding: pending });
    });
    return () => {
      alive = false;
    };
  }, []);

  if (stage.kind === 'loading') return null;

  if (stage.kind === 'pick') {
    return (
      <DataSpacePicker
        userName={user.name}
        onLogout={logout}
        onOpen={({ id }) => {
          const selected = stage.spaces.items.find((space) => space.id === id);
          activateSpace(id, selected?.name);
          setStage(onboarding ? { kind: 'onboarding' } : { kind: 'app', spaceId: id });
        }}
      />
    );
  }

  if (stage.kind === 'onboarding') {
    return (
      <OnboardingPage
        onFinish={(exit) => {
          setStage({ kind: 'app', spaceId });
          if (exit) navigate(exit.area, exit.page, exit.id);
          else navigate('home', 'HOW_IT_WORKS');
        }}
      />
    );
  }

  return <>{children(stage.spaceId)}</>;
}
