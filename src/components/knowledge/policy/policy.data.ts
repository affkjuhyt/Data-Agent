import { FilterPolicyType } from '../../../lib/enums';

export type { FilterPolicyType };

export interface FilterPolicy {
  id: string;
  name: string;
  type: FilterPolicyType;
  content: string;
  added_by: string;
  added_when: string;
  active: boolean;
}

export const INITIAL_FILTER_POLICIES: FilterPolicy[] = [
  {
    id: 'FP-001',
    name: 'Exclude PII Data',
    type: 'natural_language',
    content:
      'Remove any personally identifiable information including names, email addresses, phone numbers, and social security numbers before promoting to Silver layer.',
    added_by: 'platform-admin',
    added_when: '2026-05-20',
    active: true,
  },
  {
    id: 'FP-002',
    name: 'Remove Duplicate Sections',
    type: 'natural_language',
    content:
      'Detect and strip semantically redundant paragraphs or repeated boilerplate text blocks found across multiple document sections.',
    added_by: 'ai-engineer',
    added_when: '2026-05-21',
    active: true,
  },
  {
    id: 'FP-003',
    name: 'Compliance Keywords Block',
    type: 'exact_word',
    content: JSON.stringify(['DRAFT', 'INTERNAL USE ONLY', 'NOT FOR DISTRIBUTION', 'CONFIDENTIAL', 'WIP']),
    added_by: 'platform-admin',
    added_when: '2026-05-22',
    active: true,
  },
  {
    id: 'FP-004',
    name: 'Internal Draft Filter',
    type: 'natural_language',
    content:
      'Exclude documents marked as internal drafts or work-in-progress that have not been officially reviewed or approved by a team lead.',
    added_by: 'ai-engineer',
    added_when: '2026-05-23',
    active: false,
  },
];

export const BASE_EXTRACTION_POLICY = `Extract all named entities including:
• Organizations (ORG): companies, institutions, agencies, departments
• Persons (PER): individuals, roles, job titles, team names
• Locations (LOC): geographic references, addresses, regions
• Dates & Times (DATE): temporal references, schedules, deadlines
• Concepts (CONCEPT): domain-specific abstract terms and definitions

Extract directed relationships between entities:
• WORKS_FOR, LOCATED_IN, PART_OF, REPORTS_TO
• CAUSED_BY, RESULTS_IN, RELATED_TO
• COMPLIES_WITH, GOVERNS, REFERENCES, SUPERSEDES

Storage target: Qdrant vector store + graph metadata overlay
Embedding model: platform default (text-embedding-3-large)
Chunk strategy: semantic boundary detection · 512-token max
Relationship confidence threshold: 0.72`;
