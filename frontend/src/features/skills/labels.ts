import type { McpServer, Skill, SkillVersionStatus, ToolLevel } from '../../lib/v1/skills';
import { formatNumber } from '../../lib/formatVN';

type Tone = 'success' | 'warning' | 'error' | 'info' | 'idle';

export const SKILL_TONE: Record<Skill['status'], Tone> = {
  certified: 'success',
  pending_review: 'warning',
  testing: 'warning',
  draft: 'warning',
  deprecated: 'idle',
};

export const VERSION_TONE: Record<SkillVersionStatus, Tone> = {
  active: 'success',
  pending_review: 'warning',
  changes_requested: 'warning',
  draft: 'warning',
  deprecated: 'idle',
};

/*
 * The four permission levels and the approval policy each carries ("Công cụ theo mức quyền").
 * The policy is the platform's rule per level, not data of a tool, so it lives here.
 */
export const TOOL_LEVELS: {
  level: ToolLevel;
  label: string;
  short: string;
  tone: Tone;
  policy: string;
}[] = [
  {
    level: 'read',
    label: 'Đọc',
    short: 'Đọc',
    tone: 'success',
    policy: 'Tự do trong phạm vi dữ liệu của agent',
  },
  {
    level: 'write_derived',
    label: 'Ghi bản derived',
    short: 'Ghi bản derived',
    tone: 'warning',
    policy: 'Luôn tạo bản mới, có rollback; cần duyệt theo cấu hình agent',
  },
  {
    level: 'external',
    label: 'Gửi ra ngoài',
    short: 'Gửi ra ngoài',
    tone: 'error',
    policy: 'Bắt buộc duyệt nếu người nhận ngoài tổ chức',
  },
  {
    level: 'system',
    label: 'Hành động hệ thống',
    short: 'Hệ thống',
    tone: 'error',
    policy: 'Chỉ quản trị viên; agent không được gán',
  },
];

export const levelOf = (level: ToolLevel) =>
  TOOL_LEVELS.find((l) => l.level === level) ?? TOOL_LEVELS[0];

export const MCP_AUTH: Record<McpServer['auth'], string> = {
  oauth2: 'OAuth2',
  sso: 'SSO',
  api_key: 'API key',
};

export const MCP_STATUS: Record<McpServer['status'], { label: string; tone: Tone }> = {
  active: { label: 'Hoạt động', tone: 'success' },
  auth_error: { label: 'Lỗi xác thực', tone: 'error' },
  pending_pii_review: { label: 'Chờ duyệt PII', tone: 'warning' },
};

/** "412 lượt"; a dash when the skill never ran. */
export const runsText = (runs: number | null) =>
  runs === null ? '–' : `${formatNumber(runs)} lượt`;
