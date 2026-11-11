import type { Citation } from '../types';

/**
 * Right-hand passage for a selected document citation (ported from the old DOC_ASK aside).
 * Clicking "Mở đoạn gốc" opens the document viewer on the cited page.
 */
export function CitationPassagePanel({
  citation,
  onOpen,
  onClose,
}: {
  citation: Citation | null;
  onOpen: (citation: Citation) => void;
  onClose: () => void;
}) {
  if (!citation || citation.type !== 'document') return null;
  return (
    <aside
      aria-label="Đoạn gốc"
      className="flex w-[360px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-xl pb-da-xl pt-[20px] max-[1023px]:hidden"
    >
      <div className="flex items-start justify-between gap-da-sm">
        <h2 className="m-0 text-foreground da-heading-3">
          [{citation.index}] {citation.source}
        </h2>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 text-muted-foreground da-caption hover:text-foreground"
        >
          Đóng
        </button>
      </div>
      <blockquote className="m-0 border-l-2 border-border bg-card px-[14px] py-[10px] text-foreground da-body">
        {citation.snippet}
      </blockquote>
      {citation.doc_id && (
        <button
          type="button"
          className="self-start text-primary da-body-strong hover:underline"
          onClick={() => onOpen(citation)}
        >
          Mở đoạn gốc
        </button>
      )}
      <p className="m-0 text-muted-foreground da-body-small">
        Đoạn được tô là phần agent dùng để trả lời. Bấm “Mở đoạn gốc” để xem trong trình xem tài liệu,
        đúng trang.
      </p>
    </aside>
  );
}
