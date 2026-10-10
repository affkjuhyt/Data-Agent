import { useEffect, useState } from 'react';
import { Checkbox } from '../../design-system/Checkbox';
import { applyMartSuggestion, type DataMart } from '../../lib/v1/dataProducts';
import { FormDialog } from '../admin-access/FormDialog';

/*
 * "Hộp thoại · suggestMart" (2095:9307): the dimensions and measures the agent proposes adding to
 * a mart, from frequent questions and certified metrics. Each is ticked by default; unticking one
 * leaves it out (an unticked suggested dimension is dropped from the model).
 */
export function SuggestMartDialog({
  open,
  mart,
  onClose,
  onApplied,
}: {
  open: boolean;
  mart: DataMart;
  onClose: () => void;
  onApplied: () => void;
}) {
  const suggestion = mart.suggestion;
  const all = [...(suggestion?.dimensions ?? []), ...(suggestion?.measures ?? [])];
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setPicked(new Set(all.map((x) => x.key)));
    setError(undefined);
    // Reset only when the dialog opens.
  }, [open]);

  const toggle = (key: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });

  const submit = async () => {
    if (!suggestion) return;
    setSending(true);
    setError(undefined);
    try {
      await applyMartSuggestion(mart.id, {
        dimensions: suggestion.dimensions.filter((d) => picked.has(d.key)).map((d) => d.key),
        measures: suggestion.measures.filter((d) => picked.has(d.key)).map((d) => d.key),
      });
      onApplied();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  const group = (label: string, items: { key: string; label: string }[]) =>
    items.map((item) => (
      <div key={item.key} className="flex flex-col gap-da-xs">
        <p className="text-foreground da-label">{label}</p>
        <div className="rounded-md border border-border px-da-md py-[7px]">
          <Checkbox checked={picked.has(item.key)} onChange={(on) => toggle(item.key, on)}>
            {item.label}
          </Checkbox>
        </div>
      </div>
    ));

  return (
    <FormDialog
      open={open}
      title="Agent đề xuất data mart"
      description="Từ các câu hỏi thường gặp và chỉ số đã chứng nhận."
      // copy: provisional, Figma 2095:9342 unread (rate limit)
      submitLabel="Thêm vào mart"
      loading={sending}
      error={error}
      onSubmit={submit}
      onCancel={onClose}
    >
      {group('Thêm chiều', suggestion?.dimensions ?? [])}
      {group('Thêm độ đo', suggestion?.measures ?? [])}
    </FormDialog>
  );
}
