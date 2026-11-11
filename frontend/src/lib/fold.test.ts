import { describe, expect, it } from 'vitest';
import { fold, folds } from './fold';

describe('fold', () => {
  it('drops case and marks, and reads đ as d', () => {
    expect(fold('Đà Nẵng · Chi nhánh')).toBe('da nang · chi nhanh');
  });

  it('finds Vietnamese typed without its marks', () => {
    expect(folds('Chi nhánh Đà Nẵng', 'da nang')).toBe(true);
    expect(folds('Chi nhánh Đà Nẵng', 'CHI NHANH')).toBe(true);
    expect(folds('Hợp đồng', 'hop dong')).toBe(true);
    expect(folds('Hợp đồng', 'khach')).toBe(false);
  });
});
