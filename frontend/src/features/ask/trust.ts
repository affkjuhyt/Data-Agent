/**
 * The trust label every answer carries (invariant 14; docs/v3/01-agent-core-and-ui.md §5.5).
 *
 * Each query result has its own label; the answer's badge is the weakest one, with a breakdown
 * when they differ ("2 kết quả Có quản trị · 1 kết quả Khám phá"). Scale for numbers:
 * Đã kiểm > Có quản trị > Khám phá > Chưa đủ bằng chứng. An answer built only from documents
 * is "Giả thuyết từ tài liệu" — a separate label, not a step on the number scale.
 */
import type { Citation, EvidenceRun } from './types';

export type TrustLevel = 'VERIFIED' | 'GOVERNED' | 'EXPLORATORY' | 'INSUFFICIENT';

export interface AnswerTrust {
  level: TrustLevel | 'DOCUMENTS';
  label: string;
  tone: 'success' | 'primary' | 'warning' | 'info' | 'neutral';
  /** set when the results do not all carry the same label */
  breakdown?: string;
  /** one sentence on what the label means, for the tooltip */
  meaning: string;
}

const RANK: TrustLevel[] = ['VERIFIED', 'GOVERNED', 'EXPLORATORY', 'INSUFFICIENT'];

const LEVELS: Record<TrustLevel, Omit<AnswerTrust, 'level' | 'breakdown'>> = {
  VERIFIED: { label: 'Đã kiểm', tone: 'success', meaning: 'Số từ truy vấn đã được kiểm và duyệt.' },
  GOVERNED: { label: 'Có quản trị', tone: 'primary', meaning: 'Số tính từ chỉ số đã duyệt trong lớp ngữ nghĩa.' },
  EXPLORATORY: { label: 'Khám phá', tone: 'warning', meaning: 'Câu lệnh do mô hình viết, chưa khớp chỉ số đã duyệt. Kiểm lại trước khi dùng để quyết định.' },
  INSUFFICIENT: { label: 'Chưa đủ bằng chứng', tone: 'neutral', meaning: 'Câu trả lời không dựa trên truy vấn dữ liệu hay tài liệu nào.' },
};

/** A run's own label: the backend's `trust`, or what its source implies when it sent none. */
export function runTrust(run: Pick<EvidenceRun, 'trust' | 'generated_by'>): TrustLevel {
  if (run.trust === 'VERIFIED' || run.trust === 'GOVERNED' || run.trust === 'EXPLORATORY') return run.trust;
  if (run.generated_by === 'verified_query') return 'VERIFIED';
  if (run.generated_by === 'semantic_layer') return 'GOVERNED';
  // An unknown label from a newer backend is treated as the weakest known one, never stronger.
  return 'EXPLORATORY';
}

export function trustLabel(level: TrustLevel): string {
  return LEVELS[level].label;
}

export function answerTrust(runs: EvidenceRun[], citations: Citation[] = []): AnswerTrust {
  if (!runs.length) {
    if (citations.length) {
      return {
        level: 'DOCUMENTS',
        label: 'Giả thuyết từ tài liệu',
        tone: 'info',
        meaning: 'Câu trả lời lấy từ tài liệu, không phải từ số liệu đã tính.',
      };
    }
    return { level: 'INSUFFICIENT', ...LEVELS.INSUFFICIENT };
  }
  const levels = runs.map(runTrust);
  const weakest = levels.reduce((a, b) => (RANK.indexOf(b) > RANK.indexOf(a) ? b : a));
  const counts = RANK.map(level => [level, levels.filter(l => l === level).length] as const).filter(([, n]) => n > 0);
  return {
    level: weakest,
    ...LEVELS[weakest],
    breakdown: counts.length > 1 ? counts.map(([level, n]) => `${n} kết quả ${LEVELS[level].label}`).join(' · ') : undefined,
  };
}
