/**
 * Human approval queue — the HITL screen. Fully wired to
 * GET /api/reviews/pending and POST /api/reviews/{id}/resume; until the IA
 * change it was not reachable from any route. Cream theme, like the rest of
 * the product (it was drawn dark-on-dark for a panel that never shipped).
 */
import { useEffect, useState, useCallback } from 'react';
import { Eye, Clock, CheckCircle, XCircle, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import { apiGet, apiMutate } from '../../lib/apiClient';
import { formatDuration } from '../../lib/formatVN';

interface PendingReview {
  conv_id:      string;
  agent_id:     string;
  query:        string;
  waiting_secs: number;
  payload: {
    title:       string;
    description: string;
    detail:      unknown;
    query?:      string;
  } | null;
}

/* ─── Review Detail Card ──────────────────────────────────────────── */
function ReviewCard({ review, onResolved }: { review: PendingReview; onResolved: () => void }) {
  const [note, setNote]         = useState('');
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);

  const submit = async (approved: boolean) => {
    setLoading(true);
    setError(null);
    try {
      await apiMutate('POST', `/api/reviews/${review.conv_id}/resume`, { approved, note });
      onResolved();
    } catch (e) {
      setError((e as Error).message || 'Không gửi được quyết định.');
    } finally {
      setLoading(false);
    }
  };

  const p = review.payload;

  return (
    <div className="content-card" style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div className="font-mono text-[10px] text-[#5F5F5F] mb-1">
            {review.agent_id} · chờ {formatDuration(review.waiting_secs * 1000)}
          </div>
          <div className="text-[13px] font-semibold text-[#111111] mb-1.5">
            {p?.title ?? 'Chờ phê duyệt'}
          </div>
          <div className="text-[12px] text-[#3F3F3F] leading-relaxed">
            <span className="text-[#777777] mr-1.5">Câu hỏi:</span>
            {review.query}
          </div>
        </div>
        <span className="text-[9px] font-bold px-2 py-0.5 rounded bg-[#F3E2A7] text-[#5C3900] border border-[#B88719] whitespace-nowrap shrink-0">
          ĐANG CHỜ
        </span>
      </div>

      {p?.description && (
        <div className="font-mono text-[12px] text-[#2A2A2A] leading-relaxed bg-[#FCFBF7] border border-[#D6C79F] rounded-md px-3.5 py-2.5 whitespace-pre-wrap break-words">
          {p.description}
        </div>
      )}

      {p?.detail != null && (
        <button
          type="button"
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-1 self-start text-[11px] font-mono text-[#5F5F5F] hover:text-[#111111]"
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? 'Ẩn chi tiết' : 'Xem chi tiết'}
        </button>
      )}
      {expanded && p?.detail != null && (
        <pre className="font-mono text-[10px] text-[#2A2A2A] bg-[#F7F4EA] border border-[#D6C79F] rounded-md px-3 py-2.5 m-0 whitespace-pre-wrap overflow-x-auto max-h-[200px]">
          {JSON.stringify(p.detail, null, 2)}
        </pre>
      )}

      <div>
        <div className="text-[10px] text-[#5F5F5F] mb-1.5">Ghi chú (tuỳ chọn, gửi kèm khi từ chối)</div>
        <textarea
          value={note}
          onChange={e => setNote(e.target.value)}
          placeholder="Lý do từ chối hoặc ghi chú cho agent…"
          className="w-full bg-white border border-[#BFA66A] rounded-md px-2.5 py-2 text-[12px] text-[#111111] font-mono resize-y min-h-[60px] outline-none focus:border-[#8A5A00]"
        />
      </div>

      {error && <div className="text-[11px] text-[#A83A32]">{error}</div>}

      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button
          type="button"
          disabled={loading}
          onClick={() => submit(false)}
          className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-[12px] font-semibold bg-[#FEE2E2] border border-[#FCA5A5] text-[#7A1010] hover:bg-[#FAD7D7] disabled:opacity-50"
        >
          <XCircle size={13} /> Từ chối
        </button>
        <button
          type="button"
          disabled={loading}
          onClick={() => submit(true)}
          className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-[12px] font-semibold bg-[#DCEFE3] border border-[#1D6F4C] text-[#14502F] hover:bg-[#CDE9D8] disabled:opacity-50"
        >
          <CheckCircle size={13} /> {loading ? 'Đang xử lý…' : 'Phê duyệt'}
        </button>
      </div>
    </div>
  );
}

/* ─── Review Center ───────────────────────────────────────────────── */
export default function ReviewCenter() {
  const [reviews, setReviews] = useState<PendingReview[]>([]);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);

  const fetch_ = useCallback(async () => {
    try {
      const data = await apiGet<PendingReview[]>('/api/reviews/pending');
      setReviews(data);
      setOffline(false);
    } catch {
      setOffline(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetch_(); }, [fetch_]);

  // auto-refresh every 10s
  useEffect(() => {
    const t = setInterval(fetch_, 10_000);
    return () => clearInterval(t);
  }, [fetch_]);

  return (
    <div style={{ maxWidth: 760 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-[#B88719] font-mono mb-1.5">
            <Eye size={13} /> Vận hành / Chờ duyệt
          </div>
          <h2 className="font-display text-xl font-bold text-[#111111] m-0">Hàng đợi phê duyệt</h2>
          <p className="text-[12px] text-[#5F5F5F] mt-1 m-0">
            Các luồng agent đang dừng chờ người có thẩm quyền duyệt trước khi tiếp tục
          </p>
        </div>
        <button
          type="button"
          onClick={() => { setLoading(true); fetch_(); }}
          className="btn-secondary"
        >
          <RefreshCw size={12} /> Làm mới
        </button>
      </div>

      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[0, 1, 2].map(i => (
            <div key={i} className="pulse-soft" style={{ height: 120, borderRadius: 12, background: '#F4E8C3', opacity: 1 - i * 0.2 }} />
          ))}
        </div>
      ) : reviews.length === 0 ? (
        <div className="content-card text-center py-14 px-5 text-[#5F5F5F] text-[13px]">
          <Clock size={28} className="mx-auto mb-3 text-[#BFA66A]" />
          <div>{offline ? 'Không kết nối được workflow-runtime — thử lại sau.' : 'Không có yêu cầu nào đang chờ duyệt'}</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {reviews.map(r => (
            <ReviewCard
              key={r.conv_id}
              review={r}
              onResolved={() => setReviews(prev => prev.filter(x => x.conv_id !== r.conv_id))}
            />
          ))}
        </div>
      )}
    </div>
  );
}
