import { describe, expect, it } from 'vitest';
import { bands, brier, summary } from './calibration';

describe('calibration', () => {
  // The worked example of G10.5: 12 theses, mean confidence 78%, 7 right (58%), Brier 0.27.
  const twelve = [
    { confidence: 0.55, correct: false },
    { confidence: 0.6, correct: true },
    { confidence: 0.65, correct: false },
    { confidence: 0.7, correct: true },
    { confidence: 0.75, correct: true },
    { confidence: 0.75, correct: true },
    { confidence: 0.78, correct: false },
    { confidence: 0.85, correct: true },
    { confidence: 0.85, correct: false },
    { confidence: 0.88, correct: false },
    { confidence: 0.88, correct: true },
    { confidence: 0.92, correct: true },
  ];

  it('scores Brier against outcomes and reads over-confidence', () => {
    const s = summary(twelve);
    expect(s.count).toBe(12);
    expect(s.correct).toBe(7);
    expect(Math.round((s.meanConfidence ?? 0) * 100)).toBe(76);
    expect(s.brier).toBeCloseTo(brier(twelve)!, 10);
    expect((s.overconfidence ?? 0) > 15).toBe(true);
    expect(s.enough).toBe(true);
  });

  it('splits into the five confidence bands', () => {
    expect(bands(twelve).map((b) => [b.label, b.count, b.correct])).toEqual([
      ['50–59%', 1, 0],
      ['60–69%', 2, 1],
      ['70–79%', 4, 3],
      ['80–89%', 4, 2],
      ['90–99%', 1, 1],
    ]);
  });

  it('a perfect 50% guesser scores the 0.25 baseline', () => {
    expect(
      brier([
        { confidence: 0.5, correct: true },
        { confidence: 0.5, correct: false },
      ]),
    ).toBe(0.25);
    expect(brier([])).toBeNull();
  });
});
