import { describe, expect, it } from 'vitest';
import { sqlDiagnostics } from './sqlSyntax';

describe('sql diagnostics', () => {
  const tables = [{ name: 'mart.loans_dedup_0930' }];

  it('accepts a readable statement and marks a table the catalog does not have', () => {
    expect(sqlDiagnostics('SELECT region FROM mart.loans_dedup_0930', tables)).toEqual([]);
    expect(sqlDiagnostics('SELECT * FROM secret.accounts', tables)[0]?.message).toBe(
      'Không có bảng secret.accounts',
    );
  });

  it('marks a token the parser cannot place', () => {
    const marks = sqlDiagnostics('(((', tables);
    expect(marks.length).toBeGreaterThan(0);
    expect(marks[0]?.message).toBe('Cú pháp SQL không hợp lệ');
  });
});
