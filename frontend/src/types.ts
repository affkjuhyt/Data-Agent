import type {
  Tier,
  DocStatus,
} from './lib/enums';

export type Role = 'PLATFORM_ADMIN' | 'AI_ENGINEER' | 'EXECUTIVE';
export type Industry = 'GENERAL' | 'GOVERNMENT' | 'BANKING' | 'RAILWAY' | 'HEALTHCARE';
export type Environment = 'DEV' | 'UAT' | 'STAGING' | 'PROD';

export interface PagedResponse<T> {
  items: T[];
  page?: number;
  page_size: number;
  has_next: boolean;
  total?: number;
  next_cursor?: string | null;
}

// Canonical layer values match the postgres KBTier enum (lowercase).
export type KnowledgeLayer = Tier;
export type { DocStatus };

export interface TenantContext {
  organization: string;
  workspace: string;
  project: string;
  environment: Environment;
}

export interface User {
  id: string;
  name: string;
  email: string;
  role: Role;
  avatar?: string;
}

export interface KnowledgeDocument {
  data_id: string;
  name: string;
  source_type: string;          // doc | web | image | video | warehouse
  extension?: string | null;
  current_tier: KnowledgeLayer; // bronze | silver | gold
  status: DocStatus;            // derived server-side from current_tier
  added_by?: string | null;     // user_id of uploader
  added_on?: string | null;     // timestamp
  abstract?: string | null;
  metadata: {
    doc_type?: string | null;   // computed from source_type + extension
    language?: string | null;
    access_role?: string | null;
    url?: string | null;
    author?: string | null;     // document's true author — NOT the uploader
    published_date?: string | null;
    warehouse_type?: string | null;
    width?: number | null;
    height?: number | null;
    color_space?: string | null;
    file_size?: number | null;
    total_frame?: number | null;
    intents?: string[] | null;  // document-level content-purpose intents (Gold)
  };
  score?: number;
  conflicts?: boolean;
}

export interface NavItem {
  id: string;
  label: string;
  icon: any;
  roles: Role[];
  industry?: Industry;
}
