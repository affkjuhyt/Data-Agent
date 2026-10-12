import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { useToast } from '../../design-system/Toast';
import { formatDateTime } from '../../lib/formatVN';
import {
  dismissSkillSuggestion,
  listSkillSuggestions,
  type SkillSuggestion,
} from '../../lib/v1/skills';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P03 · Đề xuất từ agent & chia sẻ" (2294:18728, UC-03-07): step chains the agent saw repeated
 * in sessions and runs, offered as skills. "Tạo kỹ năng" opens "Tạo kỹ năng từ phiên" on the
 * first session the chain was seen in; "Bỏ qua" tells the agent not to suggest it again for
 * 90 days. Sharing a certified skill is a row action of the library; the section below says how.
 */
export default function SkillSuggestionsPage() {
  const suggestions = useV1(listSkillSuggestions, []);
  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Kỹ năng', 'Đề xuất từ agent']}
        title="Đề xuất từ agent"
        description="Agent nhận ra các chuỗi bước được lặp lại trong phiên và lượt chạy, đề xuất đóng gói thành kỹ năng"
      />
      <div className="flex flex-col px-da-3xl pt-da-md">
        <AsyncRegion
          state={suggestions}
          what="đề xuất kỹ năng"
          skeletonRows={3}
          isEmpty={(page) => page.items.length === 0}
          empty={
            <div className="py-da-xl">
              <EmptyState
                icon="sparkles"
                title="Chưa có đề xuất nào"
                description="Khi agent thấy một chuỗi bước lặp lại nhiều lần, đề xuất hiện ở đây."
              />
            </div>
          }
        >
          {(page) => (
            <ul className="flex flex-col">
              {page.items.map((s) => (
                <SuggestionRow key={s.id} suggestion={s} onDismissed={suggestions.reload} />
              ))}
            </ul>
          )}
        </AsyncRegion>
        <section className="flex flex-col gap-da-md pb-da-xl pt-[20px]">
          <h2 className="text-foreground da-heading-3">Chia sẻ kỹ năng</h2>
          <p className="text-foreground da-body-small">
            Kỹ năng đã chứng nhận có thể chia sẻ cho Data Space khác hoặc toàn tổ chức. Bên nhận
            thấy tên, mô tả, tham số và kết quả kiểm định; kỹ năng chạy theo quyền dữ liệu của người
            dùng bên nhận. Chia sẻ toàn tổ chức cần quản trị viên duyệt.
          </p>
        </section>
      </div>
    </div>
  );
}

function SuggestionRow({
  suggestion,
  onDismissed,
}: {
  suggestion: SkillSuggestion;
  onDismissed: () => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [showOccurrences, setShowOccurrences] = useState(false);
  const [busy, setBusy] = useState(false);
  const seed = suggestion.session_ids[0];

  const dismiss = () => {
    setBusy(true);
    dismissSkillSuggestion(suggestion.id)
      .then(() => {
        toast({
          tone: 'success',
          message: `Đã bỏ qua. Agent không đề xuất lại “${suggestion.title}” trong 90 ngày.`,
        });
        onDismissed();
      })
      .catch((e: Error) => {
        setBusy(false);
        toast({ tone: 'error', message: `Không bỏ qua được: ${e.message}` });
      });
  };

  return (
    <li className="flex flex-col gap-da-md border-b border-border py-da-xl">
      <div className="flex items-start gap-[20px]">
        <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
          <h2 className="text-foreground da-heading-3">{suggestion.title}</h2>
          <p className="text-muted-foreground da-caption">{suggestion.repeat_text}</p>
          <p className="text-foreground da-body-small">{suggestion.steps.join(' → ')}</p>
          <p className="text-muted-foreground da-caption">
            Dùng bởi: {suggestion.used_by.join(', ')}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-da-md">
          <Button kind="ghost" loading={busy} onClick={dismiss}>
            Bỏ qua
          </Button>
          <Button
            aria-expanded={showOccurrences}
            onClick={() => setShowOccurrences((value) => !value)}
          >
            Xem các lần lặp
          </Button>
          <Button
            kind="primary"
            disabled={!seed}
            onClick={() => seed && navigate('agents', 'SKILL_FROM_SESSION', seed)}
          >
            Tạo kỹ năng
          </Button>
        </div>
      </div>
      {showOccurrences && (
        <ul
          aria-label={`Các lần lặp của ${suggestion.title}`}
          className="flex flex-col rounded-lg border border-border"
        >
          {suggestion.occurrences.map((o) => (
            <li
              key={o.at + o.where}
              className="flex gap-da-lg border-b border-border px-da-lg py-da-md last:border-b-0"
            >
              <span className="w-[120px] shrink-0 text-muted-foreground da-caption">
                {formatDateTime(o.at)}
              </span>
              <span className="text-foreground da-body-small">{o.where}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
