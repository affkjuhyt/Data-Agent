import { useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import {
  getOnboarding,
  completeOnboarding,
  skipOnboarding,
  tryOnboardingAgent,
  type Onboarding,
  type OnboardingStepKey,
} from '../../lib/v1/explore';
import { useV1 } from '../../lib/v1/useV1';
import { cn } from '../../lib/utils';
import { BrandBar } from './BrandBar';

/*
 * "v3.1 · Onboarding lần đầu [toàn cục]" (2076:5584): a full screen before the app shell, shown
 * while GET /v1/me/onboarding says `pending` (the server holds that flag, not the browser).
 * The four steps on top; the body of "Agent đầu tiên" as drawn: three templates, the
 * recommended one selected, and "Chạy thử <agent>" which creates it and starts a test run.
 * The other steps have no frame: their body points to the screen that already does the work
 * (Kết nối nguồn, Người dùng & vai trò). "Bỏ qua, làm sau" records the skip on the server.
 */
interface OnboardingExit {
  area: string;
  page: string;
  id?: string;
}

const ELSEWHERE: Partial<
  Record<OnboardingStepKey, { text: string; exit: OnboardingExit; cta: string }>
> = {
  domain: {
    text: 'Domain của bạn đã có trong Data Space đang dùng; quản trị viên đổi được ở Data Space.',
    exit: { area: 'admin', page: 'DATA_SPACES' },
    cta: 'Mở Data Space',
  },
  sources: {
    text: 'Nguồn dữ liệu kết nối ở màn Kết nối nguồn; hệ thống kiểm tra và chỉ đọc.',
    exit: { area: 'data', page: 'CONNECT' },
    cta: 'Mở Kết nối nguồn',
  },
  invite: {
    text: 'Mời đồng nghiệp và gán vai trò ở màn Người dùng & vai trò.',
    exit: { area: 'admin', page: 'USERS' },
    cta: 'Mở Người dùng & vai trò',
  },
};

export function OnboardingPage({ onFinish }: { onFinish: (exit?: OnboardingExit) => void }) {
  const onboarding = useV1(getOnboarding, []);
  const toast = useToast();
  const [skipping, setSkipping] = useState(false);

  const skip = async () => {
    setSkipping(true);
    try {
      await skipOnboarding();
      onFinish();
    } catch (e) {
      toast({ tone: 'error', message: `Không lưu được lựa chọn: ${(e as Error).message}` });
      setSkipping(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col items-center bg-card">
      <BrandBar>
        <button
          type="button"
          onClick={() => void skip()}
          disabled={skipping}
          className="text-muted-foreground da-label hover:text-foreground"
        >
          Bỏ qua, làm sau
        </button>
      </BrandBar>
      <div className="flex w-[880px] max-w-[calc(100vw-32px)] flex-col gap-da-xl pb-da-2xl pt-[140px] max-[1279px]:pt-da-3xl">
        <AsyncRegion state={onboarding} what="hướng dẫn bắt đầu" skeletonRows={4}>
          {(data) => <Steps data={data} onFinish={onFinish} />}
        </AsyncRegion>
      </div>
    </div>
  );
}

function Steps({
  data,
  onFinish,
}: {
  data: Onboarding;
  onFinish: (exit?: OnboardingExit) => void;
}) {
  const firstOpen = data.steps.findIndex((s) => s.state !== 'done');
  const [index, setIndex] = useState(firstOpen === -1 ? data.steps.length - 1 : firstOpen);
  const step = data.steps[index];

  return (
    <>
      <ol className="flex gap-da-sm" aria-label="Các bước">
        {data.steps.map((s, i) => (
          <li
            key={s.key}
            aria-current={i === index ? 'step' : undefined}
            className={cn(
              'flex min-w-0 flex-1 items-center gap-da-sm rounded-md border px-da-md py-da-sm',
              i === index ? 'border-primary' : 'border-border',
              s.state === 'todo' && i !== index ? 'text-muted-foreground' : 'text-foreground',
            )}
          >
            <span className={cn('da-mono', (s.state === 'done' || i === index) && 'text-primary')}>
              {s.state === 'done' ? '✓' : i + 1}
            </span>
            <span className="truncate da-body-strong">{s.label}</span>
          </li>
        ))}
      </ol>

      {step.key === 'first_agent' ? (
        <FirstAgent
          data={data}
          onBack={index > 0 ? () => setIndex(index - 1) : undefined}
          onFinish={onFinish}
        />
      ) : (
        <Elsewhere
          title={step.label}
          info={ELSEWHERE[step.key]}
          onBack={index > 0 ? () => setIndex(index - 1) : undefined}
          onNext={
            index < data.steps.length - 1
              ? () => setIndex(index + 1)
              : // The last step done: tell the server, so onboarding does not come back next sign-in.
                () => completeOnboarding().finally(() => onFinish())
          }
          onOpen={onFinish}
        />
      )}
    </>
  );
}

function FirstAgent({
  data,
  onBack,
  onFinish,
}: {
  data: Onboarding;
  onBack?: () => void;
  onFinish: (exit?: OnboardingExit) => void;
}) {
  const toast = useToast();
  const [chosen, setChosen] = useState(
    (data.templates.find((t) => t.badge.recommended) ?? data.templates[0])?.ref,
  );
  const [busy, setBusy] = useState(false);
  const template = data.templates.find((t) => t.ref === chosen);

  const run = async () => {
    if (!template) return;
    setBusy(true);
    try {
      const started = await tryOnboardingAgent(template.ref);
      onFinish({ area: 'agents', page: 'RUN', id: started.run_id });
    } catch (e) {
      toast({ tone: 'error', message: `Không chạy thử được: ${(e as Error).message}` });
      setBusy(false);
    }
  };

  return (
    <>
      <div className="flex flex-col gap-da-xs">
        <h1 className="text-foreground da-heading-1">Chọn agent đầu tiên</h1>
        <p className="text-muted-foreground da-reading">
          {data.context_text} Agent sẽ chạy thử trên dữ liệu của bạn trong khoảng 2 phút, không gửi
          gì ra ngoài.
        </p>
      </div>
      <div role="radiogroup" aria-label="Mẫu agent" className="flex gap-da-md max-[767px]:flex-col">
        {data.templates.map((t) => {
          const active = t.ref === chosen;
          return (
            <button
              key={t.ref}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setChosen(t.ref)}
              className={cn(
                'flex min-w-0 flex-1 flex-col items-start gap-da-sm rounded-lg bg-card p-da-lg text-left',
                active
                  ? 'border-2 border-primary'
                  : 'border border-border hover:border-border-strong',
              )}
            >
              <span
                className={cn(
                  'inline-flex size-8 items-center justify-center rounded-md',
                  active ? 'bg-primary text-primary-foreground' : 'bg-subtle text-foreground',
                )}
              >
                <Icon name="agent" />
              </span>
              <span className="text-foreground da-heading-3">{t.title}</span>
              <span className="text-muted-foreground da-body-small">{t.description}</span>
              {t.badge.recommended ? (
                <StatusDot tone="primary">{t.badge.label}</StatusDot>
              ) : (
                <Badge>{t.badge.label}</Badge>
              )}
            </button>
          );
        })}
      </div>
      <div className="flex items-center gap-da-sm">
        <p className="min-w-0 flex-1 text-muted-foreground da-caption">
          Bạn có thể đổi cấu hình hoặc thêm agent khác bất cứ lúc nào.
        </p>
        {onBack && (
          <Button kind="ghost" onClick={onBack}>
            Quay lại
          </Button>
        )}
        <Button kind="primary" loading={busy} disabled={!template} onClick={() => void run()}>
          Chạy thử {template?.title ?? 'agent'}
        </Button>
      </div>
    </>
  );
}

function Elsewhere({
  title,
  info,
  onBack,
  onNext,
  onOpen,
}: {
  title: string;
  info: (typeof ELSEWHERE)[OnboardingStepKey];
  onBack?: () => void;
  onNext: () => void;
  onOpen: (exit: OnboardingExit) => void;
}) {
  return (
    <>
      <h1 className="text-foreground da-heading-1">{title}</h1>
      {info && (
        <Notice
          tone="info"
          action={
            <Button size="sm" onClick={() => onOpen(info.exit)}>
              {info.cta}
            </Button>
          }
        >
          {info.text}
        </Notice>
      )}
      <div className="flex items-center justify-end gap-da-sm">
        {onBack && (
          <Button kind="ghost" onClick={onBack}>
            Quay lại
          </Button>
        )}
        <Button kind="primary" onClick={onNext}>
          Tiếp tục
        </Button>
      </div>
    </>
  );
}
