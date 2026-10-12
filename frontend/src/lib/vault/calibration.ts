/*
 * Calibration on the device (tab 5 G10.5, BR-191): confidence never leaves the device in clear, so
 * the Brier score and the table by confidence band are computed here from the decrypted theses
 * and their final scores. Only correct and incorrect count; a conclusion needs 10 of them.
 */
interface ScoredThesis {
  confidence: number;
  correct: boolean;
}

export const BRIER_BASELINE = 0.25;
export const MIN_FOR_CONCLUSION = 10;

const BANDS = [
  { label: '50–59%', from: 0.5, to: 0.6 },
  { label: '60–69%', from: 0.6, to: 0.7 },
  { label: '70–79%', from: 0.7, to: 0.8 },
  { label: '80–89%', from: 0.8, to: 0.9 },
  { label: '90–99%', from: 0.9, to: 1 },
];

export function brier(items: ScoredThesis[]): number | null {
  if (items.length === 0) return null;
  return items.reduce((s, t) => s + (t.confidence - (t.correct ? 1 : 0)) ** 2, 0) / items.length;
}

export interface Band {
  label: string;
  count: number;
  correct: number;
  meanConfidence: number | null;
  hitRate: number | null;
}

export function bands(items: ScoredThesis[]): Band[] {
  return BANDS.map((b) => {
    const inBand = items.filter((t) => t.confidence >= b.from && t.confidence < b.to);
    const correct = inBand.filter((t) => t.correct).length;
    return {
      label: b.label,
      count: inBand.length,
      correct,
      meanConfidence: inBand.length
        ? inBand.reduce((s, t) => s + t.confidence, 0) / inBand.length
        : null,
      hitRate: inBand.length ? correct / inBand.length : null,
    };
  });
}

export function summary(items: ScoredThesis[]) {
  const correct = items.filter((t) => t.correct).length;
  const meanConfidence = items.length
    ? items.reduce((s, t) => s + t.confidence, 0) / items.length
    : null;
  const hitRate = items.length ? correct / items.length : null;
  return {
    count: items.length,
    correct,
    hitRate,
    meanConfidence,
    brier: brier(items),
    /** Positive: more confident than right, in percentage points. */
    overconfidence:
      meanConfidence !== null && hitRate !== null ? (meanConfidence - hitRate) * 100 : null,
    enough: items.length >= MIN_FOR_CONCLUSION,
  };
}
