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
