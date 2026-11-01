import { describe, expect, it } from 'vitest';
import { blocksForMessage } from './answerBlocks';
import type { AskMessage, EvidenceRun } from './types';

const run: EvidenceRun = {
  id: 'run-1', kind: 'sql', sql: 'SELECT 1', generated_by: 'semantic_layer',
  columns: ['month', 'value'], rows: [{ month: '2026-01', value: 10 }],
};

describe('answer block model', () => {
  it('creates only the blocks supported by the answer data', () => {
    const blocks = blocksForMessage({
      id: 'a1', content: 'Doanh thu tăng.', status: 'done',
      chartSpecs: [{ chart_type: 'line', title: 'Doanh thu', x_key: 'month', y_keys: ['value'], data: [] }],
      evidence: { runs: [run], clarifications: [], warnings: [] },
    });

    expect(blocks.map((block) => block.type)).toEqual(['markdown', 'chart', 'table', 'sql']);
  });

  it('uses the requested layout without changing the block data', () => {
    const blocks = blocksForMessage({
      id: 'a1', content: 'Kết quả', status: 'done', layout: 'chart_first',
      chartSpecs: [{ chart_type: 'bar', title: 'Doanh thu', x_key: 'region', y_keys: ['value'], data: [] }],
    });

    expect(blocks.map((block) => block.type)).toEqual(['chart', 'markdown']);
  });
});
