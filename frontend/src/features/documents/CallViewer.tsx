import { useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { Icon } from '../../design-system/Icon';
import { StatusDot } from '../../design-system/StatusDot';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import type { CallRecording, DocumentView } from '../../lib/v1/documents';
import { FactValue, KeyValue, SidePanel } from './parts';

/*
 * "v3.1 · Phi cấu trúc · Trình xem ghi âm" (2075:5061, UC-05-01): the player with the stretch the
 * agent took the commitment from in warning, the transcript (personal data masked), and what the
 * agent extracted with the read policy. A transcript line seeks the player to it.
 */
const clock = (s: number) =>
  `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

export function CallViewer({ doc, call }: { doc: DocumentView; call: CallRecording }) {
  const { navigate } = useAppState();
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  // Opens on the line the agent took the commitment from, the reason someone opens a call.
  const [position, setPosition] = useState(() => {
    const from = call.highlight?.from_s;
    return from === undefined ? 0 : (call.transcript.find((l) => l.at_s >= from)?.at_s ?? from);
  });

  const seek = (s: number) => {
    setPosition(s);
    if (audioRef.current) audioRef.current.currentTime = s;
  };

  const toggle = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) void audio.play();
    else audio.pause();
  };

  const current = [...call.transcript].reverse().find((l) => l.at_s <= position);
  const bars = call.waveform.length;

  return (
    <div className="flex min-h-full">
      <section
        aria-label="Ghi âm"
        className="flex min-w-0 flex-1 flex-col gap-da-lg px-da-2xl py-da-xl"
      >
        <header className="flex flex-col gap-da-xs">
          <nav aria-label="Đường dẫn" className="text-muted-foreground da-caption">
            Phi cấu trúc ›{' '}
            {doc.collection && (
              <button
                type="button"
                className="hover:text-foreground hover:underline"
                onClick={() => doc.collection && navigate('data', 'COLLECTION', doc.collection.id)}
              >
                {doc.collection.name}
              </button>
            )}
          </nav>
          <div className="flex flex-wrap items-center gap-da-md">
            <h1 className="text-foreground da-heading-1">{call.title}</h1>
            <StatusDot tone={call.policy.tone}>{call.policy.text}</StatusDot>
          </div>
        </header>

        <div className="flex items-center gap-da-md rounded-lg border border-border bg-card px-da-lg py-da-md">
          {call.audio_url ? (
            <>
              <audio
                ref={audioRef}
                src={call.audio_url}
                preload="metadata"
                onLoadedMetadata={(e) => {
                  e.currentTarget.currentTime = position;
                }}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onTimeUpdate={(e) => setPosition(e.currentTarget.currentTime)}
              />
              <PlayButton playing={playing} onClick={toggle} />
            </>
          ) : (
            <Tooltip content="Bản ghi này chỉ còn văn bản chuyển từ âm thanh.">
              <PlayButton playing={false} disabled />
            </Tooltip>
          )}
          <div
            role="slider"
            tabIndex={0}
            aria-label="Vị trí phát"
            aria-valuemin={0}
            aria-valuemax={call.duration_s}
            aria-valuenow={Math.round(position)}
            aria-valuetext={clock(position)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowRight') seek(Math.min(call.duration_s, position + 5));
              if (e.key === 'ArrowLeft') seek(Math.max(0, position - 5));
            }}
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              if (rect.width > 0) seek(((e.clientX - rect.left) / rect.width) * call.duration_s);
            }}
            className="flex h-[30px] min-w-0 flex-1 cursor-pointer items-center gap-da-xs overflow-hidden"
          >
            {call.waveform.map((h, i) => {
              const t = (i / bars) * call.duration_s;
              const marked =
                call.highlight && t >= call.highlight.from_s && t <= call.highlight.to_s;
              return (
                <span
                  key={i}
                  className={cn(
                    'w-1 shrink-0 rounded-[2px]',
                    marked ? 'bg-warning' : t <= position ? 'bg-primary' : 'bg-border-strong',
                  )}
                  style={{ height: `${Math.max(0.15, h) * 100}%` }}
                />
              );
            })}
          </div>
          <span className="whitespace-nowrap text-muted-foreground da-mono">
            {clock(position)} / {clock(call.duration_s)}
          </span>
        </div>

        <ol
          aria-label="Lời thoại"
          className="flex flex-col gap-da-md rounded-lg border border-border bg-card px-da-xl py-da-lg"
        >
          {call.transcript.map((line) => (
            <li key={line.at_s}>
              <button
                type="button"
                aria-current={line === current || undefined}
                onClick={() => seek(line.at_s)}
                className={cn(
                  'flex w-full items-start gap-da-md rounded-md px-da-md py-da-sm text-left hover:bg-subtle',
                  // Figma marks the line under the playhead with a rule below it.
                  line === current && 'rounded-none border-b border-border',
                )}
              >
                <span className="w-[44px] shrink-0 text-muted-foreground da-mono">
                  {clock(line.at_s)}
                </span>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span
                    className={
                      line.role === 'customer'
                        ? 'text-primary da-label'
                        : 'text-muted-foreground da-label'
                    }
                  >
                    {line.speaker}
                  </span>
                  <span className="text-foreground da-body-small">{line.text}</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
      </section>

      <SidePanel label="Agent trích xuất" width="w-[380px]">
        <h2 className="text-muted-foreground da-overline">Agent trích xuất</h2>
        {call.facts.map((fact) => (
          <KeyValue key={fact.label} label={fact.label} labelWidth="w-[100px]">
            <FactValue fact={fact} />
          </KeyValue>
        ))}
        {call.agent_note && (
          <p className="flex items-start gap-da-md rounded-md border border-border px-da-lg py-da-md text-foreground da-body-small">
            <Icon name="agent" className="mt-px shrink-0 text-primary" />
            {call.agent_note}
          </p>
        )}
      </SidePanel>
    </div>
  );
}

function PlayButton({
  playing,
  disabled = false,
  onClick,
}: {
  playing: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={playing ? 'Tạm dừng' : 'Phát'}
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : onClick}
      className={cn(
        'inline-flex size-9 shrink-0 items-center justify-center rounded-full',
        disabled
          ? 'cursor-not-allowed bg-subtle text-muted-foreground'
          : 'bg-primary text-primary-foreground hover:bg-primary-hover',
      )}
    >
      <Icon name={playing ? 'x' : 'play'} />
    </button>
  );
}
