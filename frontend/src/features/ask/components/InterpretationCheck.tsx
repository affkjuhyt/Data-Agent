/**
 * "v3.1 · Semantic · Agent hiểu câu hỏi" (2088:7584, UC-07-09, UC-01-03): before running, the
 * agent shows how it read the question — the sentence with its pieces marked (metric, filters,
 * the ambiguous one, the period), each slot with its value, where it came from and how sure it
 * is, the readings of an ambiguous term to pick from, and the plan. Shown only when a piece is
 * below 0,8 or the person asked to always check; every answer records the reading it used.
 */
import { useState } from 'react';
import { Button } from '../../../design-system/Button';
import { Checkbox } from '../../../design-system/Checkbox';
import { Icon } from '../../../design-system/Icon';
import { StatusDot } from '../../../design-system/StatusDot';
import { cn } from '../../../lib/utils';
import type { Understanding } from '../types';

const PART = {
  metric: 'border-success/40 text-success',
  filter: 'border-info/40 text-info',
  ambiguous: 'border-warning/40 text-warning',
  period: 'border-info/40 text-info',
};
const SURE = 0.8;
const fmt = (n: number) => n.toFixed(2).replace('.', ',');

export function InterpretationCheck({
  understanding,
  disabled,
  onConfirm,
  onEdit,
}: {
  understanding: Understanding;
  disabled?: boolean;
  onConfirm: (choice: { meaning?: string; remember: boolean }) => void;
  onEdit?: () => void;
}) {
  const [meaning, setMeaning] = useState(understanding.ambiguity?.selected);
  const [remember, setRemember] = useState(false);
  const chosen = understanding.ambiguity?.options.find((o) => o.key === meaning);
  const open = understanding.check_required;

  return (
    <div className="flex flex-col gap-da-sm">
      <section
        aria-label="Kiểm tra cách hiểu"
        className="flex flex-col gap-da-md rounded-md border border-border bg-card p-da-md"
      >
        <div className="flex items-center gap-da-md">
          <Icon name="agent" className="text-muted-foreground" />
          <span className="flex flex-col">
            <span className="text-foreground da-body-small">{understanding.agent}</span>
            {open ? (
              <StatusDot tone="warning">Chờ bạn</StatusDot>
            ) : (
              <StatusDot tone="success">Đã xác nhận</StatusDot>
            )}
          </span>
          <span className="ml-auto text-muted-foreground da-caption">
            Kiểm tra cách hiểu trước khi chạy
          </span>
        </div>
        <p className="flex flex-wrap items-center gap-da-xs text-foreground da-body">
          {understanding.parts.map((p, i) =>
            p.kind ? (
              <span key={i} className={cn('rounded-sm border px-[6px] py-[2px]', PART[p.kind])}>
                {p.text}
              </span>
            ) : (
              <span key={i} className="text-muted-foreground">
                {p.text}
              </span>
            ),
          )}
        </p>
        <table className="w-full border-collapse da-body-small">
          <tbody>
            {understanding.slots.map((s) => (
              <tr
                key={`${s.slot}-${s.value}`}
                className={cn(s.divider && 'border-t border-border')}
              >
                <td className="w-[96px] py-[7px] pl-da-sm text-muted-foreground">{s.slot}</td>
                <td className="py-[7px] text-foreground da-mono">{s.value}</td>
                <td className="py-[7px] text-muted-foreground da-caption">{s.source}</td>
                <td className="w-[64px] py-[7px] pr-da-sm text-right">
                  {s.confidence === null ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    <StatusDot tone={s.confidence < SURE ? 'warning' : 'success'}>
                      {fmt(s.confidence)}
                    </StatusDot>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {understanding.ambiguity && (
          <div
            role="radiogroup"
            aria-label={`“${understanding.ambiguity.term}” theo`}
            className="flex flex-wrap items-center gap-da-sm da-body-small"
          >
            <span className="text-foreground">“{understanding.ambiguity.term}” theo:</span>
            {understanding.ambiguity.options.map((o) => (
              <button
                key={o.key}
                type="button"
                role="radio"
                aria-checked={meaning === o.key}
                disabled={!open || disabled}
                onClick={() => setMeaning(o.key)}
                className={cn(
                  'rounded-full px-da-sm py-[3px]',
                  meaning === o.key
                    ? 'text-foreground'
                    : 'border border-border text-foreground hover:bg-subtle',
                )}
              >
                {meaning === o.key ? <StatusDot tone="success">{o.label}</StatusDot> : o.label}
              </button>
            ))}
          </div>
        )}
        <pre className="m-0 whitespace-pre-wrap text-foreground da-mono">{understanding.plan}</pre>
        {open && (
          <div className="flex flex-wrap items-center gap-da-sm border-t border-border pt-da-sm">
            {chosen && understanding.ambiguity && (
              <Checkbox checked={remember} onChange={setRemember}>
                Lưu “{understanding.ambiguity.term} = {chosen.label.split(' · ')[0].toLowerCase()}”
                cho tôi
              </Checkbox>
            )}
            <span className="flex-1" />
            {onEdit && (
              <Button icon="sliders" disabled={disabled} onClick={onEdit}>
                Sửa cách hiểu
              </Button>
            )}
            <Button
              kind="primary"
              icon="play"
              disabled={disabled}
              onClick={() => onConfirm({ meaning, remember })}
            >
              Đúng rồi, chạy
            </Button>
          </div>
        )}
      </section>
      {open && (
        <p className="text-muted-foreground da-caption">
          Chỉ hiện bước này khi có thành phần độ tin &lt; 0,8 hoặc khi bạn bật “luôn kiểm tra cách
          hiểu”. Mọi câu trả lời đều ghi lại cách hiểu đã dùng.
        </p>
      )}
    </div>
  );
}
