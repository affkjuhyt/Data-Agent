import {
  Database,
  Bot,
  Activity,
  Settings,
  FlaskConical,
  ClipboardCheck,
  BarChart3,
  History,
  Cpu,
  Users,
  Package,
  GitBranch,
  Coins,
  Key,
  MessageSquare,
  MessageCircle,
  Workflow,
  Monitor,
  Building2,
  Lock,
  ShieldCheck,
  Zap,
  Code,
} from 'lucide-react';
import { Role, NavItem } from './types';

const ALL_ROLES: Role[] = ['EXECUTIVE', 'PLATFORM_ADMIN', 'AI_ENGINEER'];

export const NAV_ITEMS: NavItem[] = [
  // Order = what a person needs first. Business users land on "Hỏi dữ liệu";
  // engineers on "Xây dựng" (see AppStateContext landing). Vietnamese labels
  // for end-user modules; the builder console keeps English sub-items.
  { id: 'ask',       label: 'Hỏi dữ liệu', icon: MessageSquare, roles: ALL_ROLES },
  { id: 'knowledge', label: 'Tri thức',    icon: Database,      roles: ALL_ROLES },
  { id: 'build',     label: 'Xây dựng',    icon: Package,       roles: ALL_ROLES },
  { id: 'ops',       label: 'Vận hành',    icon: Activity,      roles: ALL_ROLES },
  { id: 'settings',  label: 'Cài đặt',     icon: Settings,      roles: ALL_ROLES },
];

export interface SubNavItem {
  id: string;
  label: string;
  icon: any;
  badge?: number | string;
  comingSoon?: boolean;
  action?: boolean;
  /** Optional heading rendered above the first item of each group. */
  group?: string;
}

/* Contextual sub-nav items shown in the secondary panel per module.
 * IDs MUST match the sub-tab IDs used inside each center component
 * so secondary-nav clicks correctly drive the in-module state.
 *
 * Modules absent here (ask, knowledge) are single-screen: no secondary nav,
 * the screen gets the full width. No `comingSoon` entries on purpose — a nav
 * full of empty items is what a demo remembers.
 */
export const MODULE_SUB_ITEMS: Record<string, SubNavItem[]> = {
  'build': [
    { id: 'AGENTS',     label: 'Agents',     icon: Bot },
    { id: 'WORKFLOWS',  label: 'Workflows',  icon: Workflow },
    { id: 'VERSIONS',   label: 'Versions',   icon: GitBranch },
    { id: 'PLAYGROUND', label: 'Thử nghiệm', icon: FlaskConical },
  ],

  'ops': [
    { id: 'CONVERSATIONS', label: 'Hội thoại', icon: MessageCircle },
    { id: 'TRACES',        label: 'Trace',     icon: History },
    { id: 'REVIEWS',       label: 'Chờ duyệt', icon: ClipboardCheck },
    { id: 'ANALYTICS',     label: 'Phân tích', icon: BarChart3 },
  ],

  // IDs = SettingsSection (components/Settings/Overview.tsx) = routes.tsx keys.
  'settings': [
    { id: 'OVERVIEW',       label: 'Overview',               icon: Monitor,     group: 'General' },
    { id: 'ORGANIZATION',   label: 'Organization',           icon: Building2,   group: 'General' },
    { id: 'IAM',            label: 'Users & Permissions',    icon: Users,       group: 'Security' },
    { id: 'AUTH',           label: 'Auth & SSO',             icon: Lock,        group: 'Security' },
    { id: 'SECRETS',        label: 'Secrets Vault',          icon: Key,         group: 'Security' },
    { id: 'SECURITY',       label: 'Security & Compliance',  icon: ShieldCheck, group: 'Security' },
    { id: 'AI_PROVIDERS',   label: 'AI Providers & Models',  icon: Cpu,         group: 'Platform' },
    { id: 'INFRASTRUCTURE', label: 'Runtime Infrastructure', icon: Zap,         group: 'Platform' },
    { id: 'STORAGE',        label: 'Storage',                icon: Database,    group: 'Platform' },
    { id: 'BILLING',        label: 'Billing',                icon: Coins,       group: 'Operations' },
    { id: 'API',            label: 'API & Developers',       icon: Code,        group: 'Operations' },
    { id: 'ADVANCED',       label: 'Advanced Platform',      icon: Settings,    group: 'Operations' },
  ],
};

export const ROLES = [
  { id: 'PLATFORM_ADMIN', label: 'Platform Admin' },
  { id: 'AI_ENGINEER',    label: 'AI Engineer' },
  { id: 'EXECUTIVE',      label: 'Executive' },
];

export const ENVIRONMENTS: { id: string }[] = [
  { id: 'DEV' },
  { id: 'UAT' },
  { id: 'STAGING' },
  { id: 'PROD' },
];
