/**
 * DF-09: a dataset's data contract (A2: GET/PUT /v1/datasets/{id}/contract). The contract's ETag
 * is its version (`W/"contract-<n>"`), so a write sends If-Match from the version it read: the api
 * answers 428 without one and 412 when someone wrote it since.
 */
import { api, ok } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];
export type Contract = Schemas['ContractView'];
export type ContractRule = Schemas['ContractRule'];
export type ContractStatus = Contract['status'];

/** The expectations a contract may require (libs/quality_vocab), with how a steward reads them. */
export const EXPECTATIONS: { value: string; label: string; columns: 'one' | 'many' | 'none' }[] = [
  { value: 'expect_column_values_to_not_be_null', label: 'Không rỗng', columns: 'one' },
  { value: 'expect_column_values_to_be_unique', label: 'Không trùng', columns: 'one' },
  {
    value: 'expect_compound_columns_to_be_unique',
    label: 'Không trùng theo khoá',
    columns: 'many',
  },
  { value: 'expect_column_values_to_be_between', label: 'Trong khoảng', columns: 'one' },
  { value: 'expect_column_values_to_be_in_set', label: 'Thuộc danh sách', columns: 'one' },
  { value: 'expect_column_values_to_match_regex', label: 'Đúng mẫu', columns: 'one' },
  { value: 'expect_column_max_to_be_between', label: 'Đủ mới', columns: 'one' },
];

export const STATUS_LABEL: Record<ContractStatus, string> = {
  none: 'Chưa có hợp đồng',
  draft: 'Bản nháp',
  active: 'Đang hiệu lực',
  broken: 'Đang hỏng',
};

const contractTag = (version: number) => `W/"contract-${version}"`;

export const getContract = (datasetId: string) =>
  ok<Contract>(api.GET('/v1/datasets/{id}/contract', { params: { path: { id: datasetId } } }));

export interface ContractDraft {
  status: 'draft' | 'active';
  schema_hash_expected: string | null;
  freshness_sla: { kind: 'max_age'; value: string } | null;
  required_rules: ContractRule[];
}

/** Write the contract over the version read; `status: 'active'` is another steward's step. */
export const putContract = (datasetId: string, version: number, draft: ContractDraft) =>
  ok<Contract>(
    api.PUT('/v1/datasets/{id}/contract', {
      params: { path: { id: datasetId }, header: { 'if-match': contractTag(version) } },
      body: draft,
    }),
  );
