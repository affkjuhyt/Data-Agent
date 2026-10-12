import { http, HttpResponse } from 'msw';
import type { RankedSource, SourceAuthority } from '../lib/v1/authorities';

/* The ranking of "P07 · Thẩm quyền nguồn" (2302:19178). */
const src = (
  source_id: string,
  name: string,
  authority_level: RankedSource['authority_level'],
  manual = false,
): RankedSource => ({ source_id, name, authority_level, manual });

const CORE = src('src-core', 'Core banking', 'level_1');
const DWH = src('src-dwh', 'Kho dữ liệu doanh nghiệp', 'level_2');
const UPLOAD = src('src-upload', 'Tải tay', 'reference', true);
const GL = src('src-gl', 'Sổ cái GL', 'level_1');

function authoritiesFixture(): SourceAuthority[] {
  const row = (
    data_kind: string,
    data_kind_label: string,
    ranking: RankedSource[],
    materiality: string | null,
    decider_name: string,
    last_conflict_text: string | null = null,
  ): SourceAuthority => ({
    id: `sa-${data_kind}`,
    data_kind,
    data_kind_label,
    ranking,
    materiality,
    decider_name,
    status: 'active',
    last_conflict_text,
  });
  return [
    row(
      'loan_balance',
      'Dư nợ, nhóm nợ',
      [CORE, DWH, UPLOAD],
      '0,1% hoặc 1 tỷ',
      'Trần Quốc An',
      '05/10: Core banking và Kho dữ liệu lệch 1,8 tỷ dư nợ chi nhánh Hà Nội (0,04%). Dưới ngưỡng trọng yếu theo %, nhưng vượt 1 tỷ, nên agent không tự chọn và tạo thẻ “So sánh hai nguồn” cho Trần Quốc An.',
    ),
    row(
      'filed_figures',
      'Số đã báo cáo NHNN',
      [src('src-period', 'Bản kỳ chốt đã đóng băng', 'level_1'), GL],
      'mọi chênh lệch',
      'Trần Quốc An',
    ),
    row(
      'card_revenue',
      'Doanh thu thẻ',
      [src('src-card', 'Hệ thống thẻ', 'level_1'), GL],
      '0,5%',
      'Lê Minh Châu',
    ),
    row(
      'branch_targets',
      'Chỉ tiêu chi nhánh',
      [
        src('src-plan', 'Tải tay của Kế hoạch', 'level_2', true),
        src('src-dwh', 'Kho dữ liệu', 'level_2'),
      ],
      'không áp dụng',
      'Lê Minh Châu',
    ),
    row(
      'customer_profile',
      'Hồ sơ khách hàng',
      [src('src-crm', 'CRM', 'level_1'), CORE],
      null,
      'Ngô Thanh Bình',
    ),
  ];
}

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createAuthorityHandlers() {
  let authorities = authoritiesFixture();
  return [
    http.get('*/v1/source-authorities', () =>
      HttpResponse.json({ items: authorities, next_page_token: '' }),
    ),
    http.put('*/v1/source-authorities', async ({ request }) => {
      const body = (await request.json()) as {
        data_kind: string;
        ranking: string[];
        materiality: string | null;
      };
      const current = authorities.find((a) => a.data_kind === body.data_kind);
      if (!current) return problem(404, 'not_found', 'Không có loại dữ liệu này.');
      const ranking = body.ranking
        .map((id) => current.ranking.find((s) => s.source_id === id))
        .filter((s): s is RankedSource => Boolean(s));
      if (ranking.length !== current.ranking.length)
        return problem(422, 'validation_failed', 'Thứ tự phải gồm đúng các nguồn hiện có.');
      // BR-121: a manual or external source may not stand above a level-1 source.
      const firstLevel1 = ranking.findIndex((s) => s.authority_level === 'level_1');
      if (firstLevel1 > 0 && ranking.slice(0, firstLevel1).some((s) => s.manual))
        return problem(
          422,
          'business_rule_violated',
          'Nguồn tải tay hoặc bên ngoài không được đứng trên nguồn cấp 1 (BR-121).',
        );
      const saved: SourceAuthority = {
        ...current,
        ranking,
        materiality: body.materiality,
        status: 'draft',
      };
      authorities = authorities.map((a) => (a.id === saved.id ? saved : a));
      return HttpResponse.json(saved);
    }),
  ];
}
