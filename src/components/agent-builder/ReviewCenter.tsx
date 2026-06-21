import React, { useEffect, useState, useCallback } from 'react';
import { Eye, Clock, CheckCircle, XCircle, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';

const API = import.meta.env.VITE_WORKFLOW_RUNTIME_URL ?? 'http://localhost:8001';

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

function fmtWait(secs: number) {
  if (secs < 60)   return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  return `${Math.floor(secs / 3600)}h`;
}

/* ─── Review Detail Card ──────────────────────────────────────────── */
function ReviewCard({ review, onResolved }: { review: PendingReview; onResolved: () => void }) {
  const [note, setNote]         = useState('');
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading]   = useState(false);

  const submit = async (approved: boolean) => {
    setLoading(true);
    try {
      await fetch(`${API}/api/reviews/${review.conv_id}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved, note }),
      });
      onResolved();
    } finally {
      setLoading(false);
    }
  };

  const p = review.payload;

  return (
    <div style={{
      background: '#111', border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: 10, padding: 20, display: 'flex', flexDirection: 'column', gap: 16,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.4)', marginBottom: 4, fontFamily: 'var(--font-mono)' }}>
            {review.agent_id} · {fmtWait(review.waiting_secs)} ago
          </div>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#fff', marginBottom: 6 }}>
            {p?.title ?? 'Chờ phê duyệt'}
          </div>
          <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.6)', lineHeight: 1.5 }}>
            <span style={{ color: 'rgba(255,255,255,0.35)', marginRight: 6 }}>Query:</span>
            {review.query}
          </div>
        </div>
        <div style={{
          fontSize: 9, fontWeight: 700, padding: '3px 8px', borderRadius: 4,
          background: 'rgba(217,184,108,0.15)', color: '#D9B86C', whiteSpace: 'nowrap', flexShrink: 0,
        }}>
          PENDING
        </div>
      </div>

      {/* Description */}
      {p?.description && (
        <div style={{
          background: 'rgba(255,255,255,0.04)', borderRadius: 6,
          padding: '10px 14px', fontSize: 12, color: 'rgba(255,255,255,0.75)',
          lineHeight: 1.6, fontFamily: 'var(--font-mono)',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        }}>
          {p.description}
        </div>
      )}

      {/* Detail expand */}
      {p?.detail && (
        <button
          onClick={() => setExpanded(v => !v)}
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            background: 'none', border: 'none', cursor: 'pointer',
            color: 'rgba(255,255,255,0.4)', fontSize: 11,
            fontFamily: 'var(--font-mono)', padding: 0, alignSelf: 'flex-start',
          }}
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? 'Ẩn chi tiết' : 'Xem chi tiết'}
        </button>
      )}
      {expanded && p?.detail && (
        <pre style={{
          background: '#0A0A0A', border: '1px solid rgba(255,255,255,0.06)',
          borderRadius: 6, padding: '10px 12px', fontSize: 10,
          fontFamily: 'var(--font-mono)', color: 'rgba(229,231,235,0.7)',
          margin: 0, whiteSpace: 'pre-wrap', overflowX: 'auto', maxHeight: 200,
        }}>
          {JSON.stringify(p.detail, null, 2)}
        </pre>
      )}

      {/* Note */}
      <div>
        <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)', marginBottom: 6 }}>
          Ghi chú (tuỳ chọn, gửi khi reject)
        </div>
        <textarea
          value={note}
          onChange={e => setNote(e.target.value)}
          placeholder="Lý do từ chối hoặc ghi chú cho agent..."
          style={{
            width: '100%', background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.1)', borderRadius: 6,
            padding: '8px 10px', fontSize: 12, color: '#fff',
            fontFamily: 'var(--font-mono)', resize: 'vertical',
            minHeight: 60, outline: 'none', boxSizing: 'border-box',
          }}
        />
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button
          disabled={loading}
          onClick={() => submit(false)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '7px 16px', borderRadius: 6,
            background: 'rgba(248,113,113,0.12)', border: '1px solid rgba(248,113,113,0.3)',
            color: '#F87171', fontSize: 12, fontWeight: 600, cursor: 'pointer',
            opacity: loading ? 0.5 : 1,
          }}
        >
          <XCircle size={13} /> Từ chối
        </button>
        <button
          disabled={loading}
          onClick={() => submit(true)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '7px 16px', borderRadius: 6,
            background: 'rgba(52,211,153,0.12)', border: '1px solid rgba(52,211,153,0.3)',
            color: '#34D399', fontSize: 12, fontWeight: 600, cursor: 'pointer',
            opacity: loading ? 0.5 : 1,
          }}
        >
          <CheckCircle size={13} /> Phê duyệt
        </button>
      </div>
    </div>
  );
}

/* ─── ReviewCenter ────────────────────────────────────────────────── */
export default function ReviewCenter() {
  const [reviews, setReviews] = useState<PendingReview[]>([]);
  const [loading, setLoading] = useState(true);

  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/reviews/pending`);
      const data = await r.json();
      setReviews(data);
    } catch {
      /* ignore */
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
    <div style={{ padding: '24px 28px', maxWidth: 760 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            color: '#B88719', fontSize: 10, fontWeight: 700,
            fontFamily: 'var(--font-mono)', letterSpacing: '0.14em',
            textTransform: 'uppercase', marginBottom: 6,
          }}>
            <Eye size={13} /> Build / Reviews
          </div>
          <h2 style={{ fontSize: 20, fontWeight: 700, fontFamily: 'var(--font-display)', margin: 0 }}>
            Human Approval Queue
          </h2>
          <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.45)', marginTop: 4 }}>
            Các workflow đang chờ người duyệt trước khi tiếp tục
          </p>
        </div>
        <button
          onClick={() => { setLoading(true); fetch_(); }}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 6, padding: '6px 12px', color: 'rgba(255,255,255,0.6)',
            fontSize: 11, cursor: 'pointer',
          }}
        >
          <RefreshCw size={11} /> Refresh
        </button>
      </div>

      {/* Content */}
      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[0, 1, 2].map(i => (
            <div key={i} className="pulse-soft" style={{
              height: 120, borderRadius: 10,
              background: 'rgba(255,255,255,0.04)', opacity: 1 - i * 0.2,
            }} />
          ))}
        </div>
      ) : reviews.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '60px 20px',
          color: 'rgba(255,255,255,0.3)', fontSize: 13,
        }}>
          <Clock size={28} style={{ marginBottom: 12, opacity: 0.3 }} />
          <div>Không có yêu cầu nào đang chờ duyệt</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {reviews.map(r => (
            <ReviewCard
              key={r.conv_id}
              review={r}
              onResolved={() => {
                setReviews(prev => prev.filter(x => x.conv_id !== r.conv_id));
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
