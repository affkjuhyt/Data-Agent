import { useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import { listQualityRules, type CheckState, type QualityRule } from '../../lib/v1/dataProducts';
import { useV1 } from '../../lib/v1/useV1';
import { AddRuleDialog } from './AddRuleDialog';

/*
 * "P06 · Quy tắc chất lượng" (2290:18318, UC-06-07): every rule run after each load and each
 * pipeline run, with its level (Chặn / Cảnh báo), the last seven days as a strip, the current
 * result and its owner. "Agent gợi ý quy tắc" is not drawn: there is no suggestion endpoint and
 * no dialog for it in the design.
 */
const CURRENT: Record<CheckState, { tone: 'success' | 'warning' | 'error'; label: string }> = {
  pass: { tone: 'success', label: 'Đạt' },
  warn: { tone: 'warning', label: 'Cảnh báo' },
  fail: { tone: 'error', label: 'Vi phạm' },
};

const DAY: Record<CheckState, string> = {
  pass: 'bg-success opacity-50',
  warn: 'bg-warning',
  fail: 'bg-error',
};

export default function QualityRulesPage() {
  const rules = useV1(listQualityRules, []);
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const items = rules.data?.items ?? [];
  const failing = items.filter((r) => r.current && r.current.state !== 'pass').length;

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Chất lượng']}
        title="Quy tắc chất lượng dữ liệu"
        description={
          rules.data
            ? `${items.length} quy tắc · chạy sau mỗi lần nạp và mỗi lần pipeline chạy · ${failing} đang vi phạm`
            : undefined
        }
        actions={
          <Button kind="primary" onClick={() => setAdding(true)}>
            Thêm quy tắc
          </Button>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl">
        <AsyncRegion
          state={rules}
          what="quy tắc chất lượng"
          skeletonRows={5}
          isEmpty={(d) => d.items.length === 0}
          empty={
            <EmptyState
              icon="shield"
              title="Chưa có quy tắc chất lượng"
              description="Thêm quy tắc để kiểm tra dữ liệu sau mỗi lần nạp."
            />
          }
        >
          {(d) => (
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="px-da-lg py-da-md font-normal">Quy tắc</th>
                  <th className="w-[232px] px-da-lg py-da-md font-normal">Áp vào</th>
                  <th className="w-[102px] px-da-lg py-da-md font-normal">Mức</th>
                  <th className="w-[142px] px-da-lg py-da-md font-normal">7 ngày</th>
                  <th className="w-[182px] px-da-lg py-da-md font-normal">Hiện tại</th>
                  <th className="w-[154px] px-da-lg py-da-md font-normal">Người phụ trách</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((r) => (
                  <RuleRow key={r.id} rule={r} />
                ))}
              </tbody>
            </table>
          )}
        </AsyncRegion>
        <div className="flex flex-col gap-da-sm rounded-md bg-background px-da-xl py-da-lg text-foreground da-body-small">
          <p className="da-body-strong">Mức “Chặn” và “Cảnh báo” khác nhau thế nào</p>
          <p>
            Chặn: dữ liệu mới không được dùng; chỉ số giữ giá trị lần đạt gần nhất và ghi rõ “chưa
            cập nhật”. Cảnh báo: dữ liệu vẫn dùng, người phụ trách nhận thông báo, số liệu liên quan
            có dấu cảnh báo.
          </p>
        </div>
        <p className="text-muted-foreground da-caption">
          UC-06-07 · agent kiểm tra sau mỗi lần nạp và báo bất thường
        </p>
      </div>
      <AddRuleDialog
        open={adding}
        onClose={() => setAdding(false)}
        onAdded={(name) => {
          setAdding(false);
          // copy: provisional, Figma Toast · created (2094:8224) unread (rate limit)
          toast({ tone: 'success', message: `Đã thêm quy tắc “${name}”` });
          rules.reload();
        }}
      />
    </div>
  );
}

function RuleRow({ rule }: { rule: QualityRule }) {
  const current = rule.current ? CURRENT[rule.current.state] : null;
  return (
    <tr className="border-b border-border">
      <td className="px-da-lg py-[9px] text-foreground da-body-strong">{rule.name}</td>
      <td className="break-words px-da-lg py-[9px] text-muted-foreground da-mono">{rule.target}</td>
      <td
        className={cn(
          'px-da-lg py-[9px] da-body-small',
          rule.severity === 'block' ? 'text-foreground' : 'text-muted-foreground',
        )}
      >
        {rule.severity === 'block' ? 'Chặn' : 'Cảnh báo'}
      </td>
      <td className="px-da-lg py-[9px]">
        <span className="flex gap-[3px]" aria-label={dayLabel(rule.last_7d)} role="img">
          {rule.last_7d.map((state, i) => (
            <span
              key={i}
              className={cn('h-3 w-[14px] rounded-[2px]', state ? DAY[state] : 'bg-subtle')}
            />
          ))}
        </span>
      </td>
      <td className="px-da-lg py-[9px]">
        {current ? (
          <StatusDot tone={current.tone}>
            {current.label}
            {rule.current?.value_text ? ` · ${rule.current.value_text}` : ''}
          </StatusDot>
        ) : (
          // copy: provisional, the design has no rule that has not run yet.
          <StatusDot tone="idle">Chưa chạy</StatusDot>
        )}
      </td>
      <td className="px-da-lg py-[9px] text-muted-foreground da-body-small">{rule.owner.name}</td>
    </tr>
  );
}

// The strip is colour only, so screen readers get the count in words.
function dayLabel(days: (CheckState | null)[]) {
  const n = (s: CheckState) => days.filter((d) => d === s).length;
  return `7 ngày: ${n('pass')} đạt, ${n('warn')} cảnh báo, ${n('fail')} vi phạm`;
}
