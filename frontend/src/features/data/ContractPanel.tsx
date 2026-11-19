import { useEffect, useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { V1Error } from '../../lib/v1/client';
import {
  type Contract,
  type ContractDraft,
  type ContractRule,
  EXPECTATIONS,
  STATUS_LABEL,
  getContract,
  putContract,
} from '../../lib/v1/contracts';
import { useV1, useV1Mutation } from '../../lib/v1/useV1';

/*
 * DF-09: the data contract of one table, in Data Studio. A steward writes it (draft); another
 * steward activates it (four eyes: the api refuses its writer, unless the organisation allows
 * self-approval). An active contract is what a data product needs of its tables; a broken one
 * holds the table back (the quality gate, DF-08).
 */
const TONE = { none: 'neutral', draft: 'info', active: 'success', broken: 'error' } as const;

export function ContractPanel({ datasetId }: { datasetId: string }) {
  const contract = useV1(['getContract', datasetId], () => getContract(datasetId));
  return (
    <AsyncRegion state={contract} what="hợp đồng dữ liệu" skeletonRows={4}>
      {(data) => <ContractForm datasetId={datasetId} contract={data} />}
    </AsyncRegion>
  );
}

function why(error: Error): string {
  if (error instanceof V1Error) {
    if (error.status === 412) return 'Hợp đồng vừa được người khác sửa. Tải lại rồi làm lại.';
    if (error.status === 403 && error.code === 'policy_denied')
      return 'Người viết hợp đồng không tự kích hoạt; một người phụ trách khác kích hoạt.';
    if (error.status === 403) return 'Bạn cần quyền người phụ trách dữ liệu để sửa hợp đồng.';
  }
  return error.message;
}

/** A rule as sent: only the fields its expectation uses (one column, or the columns of a key). */
function sent(rule: ContractRule): ContractRule {
  const out: ContractRule = { expectation: rule.expectation, severity: rule.severity ?? 'high' };
  if (rule.column) out.column = rule.column;
  if (rule.columns?.length) out.columns = rule.columns;
  return out;
}

function ContractForm({ datasetId, contract }: { datasetId: string; contract: Contract }) {
  const toast = useToast();
  const [rules, setRules] = useState<ContractRule[]>(contract.required_rules ?? []);
  const [sla, setSla] = useState(contract.freshness_sla?.value ?? '');
  const [pinned, setPinned] = useState<string | null>(contract.schema_hash_expected ?? null);
  // A newer version read (after a save, or someone else's): the form shows it.
  useEffect(() => {
    setRules(contract.required_rules ?? []);
    setSla(contract.freshness_sla?.value ?? '');
    setPinned(contract.schema_hash_expected ?? null);
  }, [contract.version]);

  const save = useV1Mutation(
    (status: ContractDraft['status']) =>
      putContract(datasetId, contract.version, {
        status,
        schema_hash_expected: pinned,
        freshness_sla: sla.trim() ? { kind: 'max_age', value: sla.trim() } : null,
        required_rules: rules.map(sent),
      }),
    {
      invalidates: [['getContract', datasetId]],
      onSuccess: (saved) =>
        toast({
          tone: 'success',
          message:
            saved.status === 'active'
              ? 'Đã kích hoạt hợp đồng dữ liệu.'
              : 'Đã lưu bản nháp hợp đồng.',
        }),
      onError: (e) => toast({ tone: 'error', message: `Chưa lưu được. ${why(e)}` }),
    },
  );

  const setRule = (index: number, patch: Partial<ContractRule>) =>
    setRules((all) => all.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  const slaInvalid = sla.trim() !== '' && !/^[1-9][0-9]{0,3}[hd]$/.test(sla.trim());

  return (
    <section aria-label="Hợp đồng dữ liệu" className="flex flex-col gap-da-md">
      <div className="flex items-center gap-da-sm">
        <h2 className="text-foreground da-heading-3">Hợp đồng dữ liệu</h2>
        <Badge tone={TONE[contract.status]}>{STATUS_LABEL[contract.status]}</Badge>
        {contract.version > 0 && (
          <span className="text-muted-foreground da-caption">phiên bản {contract.version}</span>
        )}
      </div>
      {contract.status === 'broken' && (
        <Notice tone="error">
          {contract.broken_reason ?? 'Hợp đồng đang hỏng'}: bảng bị chặn cho tới khi được kích hoạt
          lại.
        </Notice>
      )}
      <Field label="Độ mới tối đa" hint="Ví dụ 26h hoặc 2d; để trống nếu không cam kết.">
        <Input
          value={sla}
          onChange={(e) => setSla(e.target.value)}
          mono
          error={slaInvalid ? 'Viết số giờ (h) hoặc số ngày (d), ví dụ 26h.' : undefined}
        />
      </Field>
      {/* Not a <Field>: a button inside its <label> would be named by the label. */}
      <div role="group" aria-label="Cấu trúc cam kết" className="flex flex-col gap-da-xs">
        <span className="text-foreground da-label">Cấu trúc cam kết</span>
        <div className="flex items-center gap-da-sm">
          <span className="text-muted-foreground da-caption">
            {pinned ? `Đã ghim ${pinned.slice(0, 12)}` : 'Chưa ghim'}
          </span>
          <Button
            kind="ghost"
            disabled={!contract.schema_hash_current}
            onClick={() => setPinned(contract.schema_hash_current ?? null)}
          >
            Ghim cấu trúc hiện tại
          </Button>
        </div>
        <span className="text-muted-foreground da-caption">
          Hợp đồng hỏng khi cấu trúc của bảng khác bản đã ghim.
        </span>
      </div>
      <div className="flex flex-col gap-da-sm">
        <span className="text-foreground da-label">Quy tắc bắt buộc</span>
        {rules.length === 0 && (
          <span className="text-muted-foreground da-caption">Chưa có quy tắc nào.</span>
        )}
        {rules.map((rule, index) => {
          const kind = EXPECTATIONS.find((e) => e.value === rule.expectation);
          return (
            <div
              key={index}
              className="flex flex-wrap items-center gap-da-sm"
              data-testid="contract-rule"
            >
              <Select
                label={`Quy tắc ${index + 1}`}
                options={EXPECTATIONS.map((e) => ({ value: e.value, label: e.label }))}
                value={rule.expectation}
                onChange={(value) => setRule(index, { expectation: value })}
              />
              {kind?.columns === 'many' ? (
                <Input
                  aria-label={`Các cột khoá của quy tắc ${index + 1}`}
                  placeholder="ma_don, dong"
                  mono
                  value={(rule.columns ?? []).join(', ')}
                  onChange={(e) =>
                    setRule(index, {
                      columns: e.target.value
                        .split(',')
                        .map((c) => c.trim())
                        .filter(Boolean),
                      column: null,
                    })
                  }
                />
              ) : (
                <Input
                  aria-label={`Cột của quy tắc ${index + 1}`}
                  placeholder="tên cột"
                  mono
                  value={rule.column ?? ''}
                  onChange={(e) => setRule(index, { column: e.target.value, columns: null })}
                />
              )}
              <Select
                label={`Mức của quy tắc ${index + 1}`}
                options={[
                  { value: 'high', label: 'Cao (chặn)' },
                  { value: 'medium', label: 'Trung bình' },
                  { value: 'low', label: 'Thấp' },
                ]}
                value={rule.severity ?? 'high'}
                onChange={(value) => setRule(index, { severity: value })}
              />
              <Button
                kind="ghost"
                onClick={() => setRules((all) => all.filter((_, i) => i !== index))}
              >
                Bỏ
              </Button>
            </div>
          );
        })}
        <div>
          <Button
            kind="ghost"
            onClick={() =>
              setRules((all) => [
                ...all,
                {
                  expectation: 'expect_column_values_to_not_be_null',
                  column: '',
                  severity: 'high',
                },
              ])
            }
          >
            Thêm quy tắc
          </Button>
        </div>
      </div>
      <div className="flex gap-da-sm">
        <Button disabled={save.pending || slaInvalid} onClick={() => save.run('draft')}>
          Lưu bản nháp
        </Button>
        <Button
          kind="primary"
          disabled={save.pending || slaInvalid || contract.status === 'none'}
          onClick={() => save.run('active')}
        >
          Kích hoạt
        </Button>
      </div>
    </section>
  );
}
