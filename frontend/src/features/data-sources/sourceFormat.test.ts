import { describe, expect, it } from 'vitest';
import { sourceRowsFixture } from '../../mocks/sources';
import {
  dataSpacesText,
  firstLoadLabel,
  formatLag,
  formatRows,
  isRestConnector,
  jobsSummary,
  modeLabel,
  scanText,
  sourcesSummary,
} from './sourceFormat';
import type { IngestionJob } from '../../lib/v1/sources';

describe('after-save estimates', () => {
  it('says the scan and first load with the estimates the test gave', () => {
    expect(scanText({ table_count: 12, scan_minutes: 4 }, 2)).toBe(
      'Quét cấu trúc 12 bảng, lập hồ sơ 2 bảng đã chọn (khoảng 4 phút)',
    );
    expect(firstLoadLabel(3)).toBe('Trong 3 giờ tới');
  });

  it('leaves out what the api did not estimate instead of saying zero', () => {
    expect(scanText({ table_count: 0, scan_minutes: null }, 0)).toBe(
      'Quét cấu trúc các bảng của nguồn',
    );
    expect(firstLoadLabel(null)).toBe('Lần nạp đầu');
  });
});

describe('isRestConnector', () => {
  it("knows the api's rest_api kind and the older rest code, nothing else", () => {
    expect(isRestConnector('rest_api')).toBe(true);
    expect(isRestConnector('rest')).toBe(true);
    expect(isRestConnector('postgres')).toBe(false);
  });
});

const byName = (name: string) => sourceRowsFixture().find((s) => s.name === name)!;

describe('sourceFormat', () => {
  it('words lag and rows as the P04 frames do', () => {
    expect(formatLag(12)).toBe('12 giây');
    expect(formatLag(6 * 3600)).toBe('6 giờ');
    expect(formatRows(1_280_000)).toBe('1,28 tr');
    expect(formatRows(412_000)).toBe('412.000');
  });

  it('names the mode and the Data Spaces of a source', () => {
    expect(modeLabel(byName('Core banking'))).toBe('Nạp về · CDC');
    expect(modeLabel(byName('DWH bán lẻ'))).toBe('Truy vấn tại chỗ');
    expect(dataSpacesText(byName('Core banking'))).toBe('Rủi ro tín dụng +2');
    expect(dataSpacesText(byName('Kho dữ liệu doanh nghiệp'))).toBe('4 Data Space');
  });

  it('summarises sources and jobs', () => {
    expect(sourcesSummary(sourceRowsFixture())).toBe('12 nguồn · 11 đúng lịch · 1 cần sửa');
    const job = (status: IngestionJob['status'], late = false) =>
      ({ status, late }) as IngestionJob;
    expect(
      jobsSummary([
        job('running'),
        job('on_time'),
        job('error', true),
        job('paused', true),
        job('on_time', true),
      ]),
    ).toBe('5 job · 2 đúng hạn · 1 trễ · 1 lỗi · 1 tạm dừng');
  });
});
