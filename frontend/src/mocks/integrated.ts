import { http, passthrough } from 'msw';
import spec from '../lib/v1/openapi.json';

/*
 * The A2 groups the screens already use on the real api (docs/v3/06-api-v1.md §5: the mock is
 * switched off group by group). With VITE_MOCK_V1=1 their requests go through to the api while
 * every other /v1 call is still answered from src/mocks; the unit tests do not load this list
 * and keep the mocks for everything.
 *
 * Identity is always real once any group is: the Data Space id in X-Data-Space comes from
 * GET /v1/data-spaces, and a fixture id there would make every real call answer 404.
 */
const INTEGRATED_GROUPS: readonly string[] = [
  'Hỏi đáp',
  'Truy vấn',
  'Cần bạn và đánh giá',
  'Phiên làm việc',
  'Agent',
  'Nguồn và danh mục',
  'Nạp dữ liệu',
  'Lớp ngữ cảnh',
  'Theo dõi, báo cáo, thông báo',
  'Quản trị',
  'Chính sách',
  'Kiểm toán',
  'Xung đột',
  'Kỳ chốt',
  'Tài liệu',
  'Kỹ năng',
  'Biến đổi dữ liệu',
  'Dự báo',
  'Gói ngành',
  'Thu thập',
  'Tích hợp',
];

const IDENTITY_OPERATIONS: readonly string[] = ['getMe', 'listMyDataSpaces'];

interface Operation {
  method: string;
  pattern: RegExp;
}

type Spec = { paths: Record<string, Record<string, { operationId?: string; tags?: string[] }>> };

/** The served operations of the integrated groups, from the api's own OpenAPI (make api-types). */
export function integratedOperations(
  groups: readonly string[] = INTEGRATED_GROUPS,
  source: Spec = spec as Spec,
): Operation[] {
  const operations: Operation[] = [];
  for (const [path, methods] of Object.entries(source.paths)) {
    // "/v1/runs/{id}:pause" → ^/v1/runs/[^/]+:pause$; the ":verb" suffix stays literal.
    const pattern = new RegExp(
      `^${path.replace(/[.*+?^$()|[\]\\]/g, '\\$&').replace(/\{[^}]+\}/g, '[^/:]+')}$`,
    );
    for (const [method, operation] of Object.entries(methods)) {
      const group = operation.tags?.[0] ?? '';
      const id = operation.operationId ?? '';
      if (groups.includes(group) || IDENTITY_OPERATIONS.includes(id))
        operations.push({ method: method.toUpperCase(), pattern });
    }
  }
  return operations;
}

/** Whether a request belongs to an integrated group; the path may sit under a gateway prefix. */
export function isIntegrated(method: string, url: string, operations = integratedOperations()) {
  const path = new URL(url).pathname.replace(/^.*?(?=\/v1\/)/, '');
  return operations.some(
    (operation) => operation.method === method && operation.pattern.test(path),
  );
}

/** Placed before every mock handler: a request of an integrated group leaves for the api. */
export function createPassthroughHandlers(operations = integratedOperations()) {
  return [
    http.all('*/v1/*', ({ request }) =>
      isIntegrated(request.method, request.url, operations) ? passthrough() : undefined,
    ),
  ];
}
