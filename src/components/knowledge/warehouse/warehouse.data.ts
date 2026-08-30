export type WarehouseType = 'snowflake' | 'databricks';
export type WizardStep = 1 | 2 | 3 | 4;

export interface TableRow {
  name: string;
  schema: string;
  rowCount: string;
  selected: boolean;
  description: string;
}

export interface FieldDef {
  key: string;
  label: string;
  placeholder: string;
  required: boolean;
  secret?: boolean;
}

export const SNOWFLAKE_FIELDS: FieldDef[] = [
  { key: 'name',      label: 'Connection Name',    placeholder: 'My Snowflake DW',                        required: true  },
  { key: 'account',   label: 'Account URL',         placeholder: 'myorg.us-east-1.snowflakecomputing.com', required: true  },
  { key: 'username',  label: 'Username',            placeholder: 'data_engineer',                          required: true  },
  { key: 'password',  label: 'Password',            placeholder: '••••••••',                               required: true, secret: true },
  { key: 'database',  label: 'Database',            placeholder: 'ANALYTICS_DB',                           required: true  },
  { key: 'schema',    label: 'Schema',              placeholder: 'PUBLIC',                                 required: false },
  { key: 'warehouse', label: 'Warehouse (Compute)', placeholder: 'COMPUTE_WH',                             required: true  },
  { key: 'role',      label: 'Role',                placeholder: 'DATA_ENGINEER',                          required: false },
];

export const DATABRICKS_FIELDS: FieldDef[] = [
  { key: 'name',        label: 'Connection Name', placeholder: 'My Databricks Cluster',          required: true  },
  { key: 'host',        label: 'Host',            placeholder: 'adb-123456.azuredatabricks.net', required: true  },
  { key: 'httpPath',    label: 'HTTP Path',       placeholder: '/sql/1.0/warehouses/abc123',     required: true  },
  { key: 'accessToken', label: 'Access Token',    placeholder: 'dapi••••••••••••••••',           required: true, secret: true },
  { key: 'catalog',     label: 'Catalog',         placeholder: 'ml_features',                    required: true  },
  { key: 'schema',      label: 'Schema',          placeholder: 'feature_store',                  required: false },
];

export const STEP_LABELS: Record<WizardStep, string> = {
  1: 'Warehouse Type',
  2: 'Credentials',
  3: 'Select Tables',
  4: 'Review',
};
