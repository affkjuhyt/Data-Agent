import {
  Database,
  Bot,
  Activity,
  Settings,
  Stethoscope,
  FlaskConical,
  ClipboardCheck,
  TrainFront,
  Building2,
  Lock,
  BarChart3,
  History,
  Cpu,
  LayoutGrid,
  Users,
  Package,
  GitBranch,
  Coins,
  Key,
  MessageSquare,
  MessageCircle,
  ScrollText,
  Workflow,
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

  'settings': [
    { id: 'TEAM',      label: 'Team & Access', icon: Users },
    { id: 'PROVIDERS', label: 'AI Providers',  icon: Cpu },
    { id: 'SECRETS',   label: 'Secrets',       icon: Key },
    { id: 'BILLING',   label: 'Billing',       icon: Coins },
    { id: 'AUDIT',     label: 'Audit Log',     icon: ScrollText },
  ],
};

export const INDUSTRIES = [
  { id: 'GENERAL',    label: 'Cross-Industry',     icon: LayoutGrid },
  { id: 'GOVERNMENT', label: 'Gov & Public',       icon: Building2 },
  { id: 'BANKING',    label: 'Financial Services', icon: Lock },
  { id: 'RAILWAY',    label: 'Railway & Metro',    icon: TrainFront },
  { id: 'HEALTHCARE', label: 'Medical & Health',   icon: Stethoscope },
];

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
