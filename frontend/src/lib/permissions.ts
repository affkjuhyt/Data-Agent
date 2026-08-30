import { useAppState } from '../AppStateContext';
import type { Role } from '../types';

type Permission =
  | 'delete_data'             // delete doc / chunk / version / table
  | 'edit_conflict'           // submit conflict resolution
  | 'process_layer'           // promote Bronze → Silver → Gold
  | 'add_data'                // open ingestion wizard
  | 'add_warehouse'           // open warehouse wizard
  | 'add_filtering_policy'    // create a new filtering policy
  | 'edit_filtering_policy'   // edit an existing filtering policy
  | 'delete_filtering_policy' // delete a filtering policy
  | 'edit_extraction_policy'  // edit the extraction policy text
  | 'toggle_qdrant'           // set Qdrant collection active / inactive
  | 'add_warehouse_config'    // create a new warehouse config
  | 'edit_warehouse_config'   // activate / change a warehouse config
  | 'add_chunk_version';      // add a new chunk version

const ALL: Permission[] = [
  'delete_data', 'edit_conflict', 'process_layer', 'add_data', 'add_warehouse',
  'add_filtering_policy', 'edit_filtering_policy', 'delete_filtering_policy',
  'edit_extraction_policy', 'toggle_qdrant', 'add_warehouse_config',
  'edit_warehouse_config', 'add_chunk_version',
];

const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  PLATFORM_ADMIN: ALL,
  AI_ENGINEER:    ['toggle_qdrant', 'edit_extraction_policy'],
  EXECUTIVE:      ['edit_conflict', 'process_layer', 'add_filtering_policy'],
};

export const usePermissions = () => {
  const { role } = useAppState();
  const perms = ROLE_PERMISSIONS[role] ?? [];
  return {
    can: (p: Permission): boolean => perms.includes(p),
    role,
  };
};
