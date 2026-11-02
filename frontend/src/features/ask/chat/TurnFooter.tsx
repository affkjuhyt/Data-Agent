import { Icon, type IconName } from '../../../design-system/Icon';
import { Menu } from '../../../design-system/Menu';

/*
 * Under a finished answer: when it was answered, and what can be done with it (design C6) —
 * "Hỏi lại" asks the same question again, "Báo sai" reports it (the existing feedback route),
 * "Sao chép" copies its text, "Biên nhận" opens its receipt when it has one. There is no
 * thumbs-up: nothing on the server would keep it.
 */

const when = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
};

function Action({ icon, label, onClick }: { icon: IconName; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-subtle hover:text-foreground"
    >
      <Icon name={icon} size={14} />
    </button>
  );
}

export function TurnFooter({
  timestamp,
  text,
  receipt,
  onRetry,
  onReport,
  onReceipt,
  onSaveKnowledge,
  onSaveSession,
}: {
  timestamp: string;
  text: string;
  receipt: boolean;
  onRetry: () => void;
  onReport: () => void;
  onReceipt: () => void;
  onSaveKnowledge?: () => void;
  onSaveSession?: () => void;
}) {
  const more = [
    ...(onSaveKnowledge ? [{ label: 'Lưu vào tri thức', icon: 'bookmark' as const, onSelect: onSaveKnowledge }] : []),
    ...(onSaveSession ? [{ label: 'Lưu vào phiên', icon: 'folder' as const, onSelect: onSaveSession }] : []),
  ];
  return (
    <div className="flex items-center gap-da-xs">
      <span className="mr-auto text-muted-foreground da-caption">{when(timestamp)}</span>
      <Action icon="refresh" label="Hỏi lại" onClick={onRetry} />
      <Action icon="alert" label="Báo sai" onClick={onReport} />
      <Action icon="copy" label="Sao chép" onClick={() => void navigator.clipboard?.writeText(text)} />
      {receipt && <Action icon="file" label="Biên nhận" onClick={onReceipt} />}
      {more.length > 0 && <Menu label="Thêm thao tác với câu trả lời" items={more} />}
    </div>
  );
}
