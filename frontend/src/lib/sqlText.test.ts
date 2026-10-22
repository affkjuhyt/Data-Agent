import { CompletionContext } from '@codemirror/autocomplete';
import { PostgreSQL, schemaCompletionSource, type SQLNamespace } from '@codemirror/lang-sql';
import { EditorState } from '@codemirror/state';
import { describe, expect, it } from 'vitest';
import {
  chartFromGrid,
  formatReadOnlySql,
  mentionedMetric,
  sqlCompletionContext,
  sqlCompletionDefaults,
  sqlErrorLine,
  sqlLines,
  sqlNumber,
  sqlBrowseTree,
  sqlSchemaTree,
  statementAround,
  unknownSqlRelations,
} from './sqlText';

describe('sql workbench text', () => {
  it('keeps every character on its line', () => {
    expect(
      sqlLines('select a\nfrom t').map((line) => line.map((token) => token.text).join('')),
    ).toEqual(['select a', 'from t']);
  });

  it('puts a clause on its own line without touching a string', () => {
    expect(formatReadOnlySql("select a from t where name = 'from here'")).toBe(
      "SELECT a\nFROM t\nWHERE name = 'from here'",
    );
  });

  it('reads a line number and a certified metric code', () => {
    expect(sqlErrorLine('syntax error at line 4')).toBe(4);
    expect(sqlErrorLine('lỗi ở dòng 2')).toBe(2);
    expect(
      mentionedMetric('select npl_ratio from t', [
        { id: 'm', code: 'NPL_RATIO', version: 4 },
        { id: 'other', code: 'NPL', version: 1 },
      ])?.id,
    ).toBe('m');
    expect(
      mentionedMetric('select npl_ratio_v2 from t', [{ id: 'm', code: 'NPL_RATIO', version: 4 }]),
    ).toBe(null);
  });

  it('names the statement under the caret and the schema the editor completes', () => {
    const script = "SELECT 1;\nDELETE FROM secret;\nSELECT ';';";
    expect(statementAround(script, 0)).toBe('SELECT 1;');
    expect(statementAround(script, script.indexOf('DELETE'))).toBe('DELETE FROM secret;');
    expect(statementAround(script, script.lastIndexOf('SELECT'))).toBe("SELECT ';';");
    expect(sqlSchemaTree([{ name: 'mart.loans', columns: [{ name: 'region' }] }])).toEqual({
      mart: { loans: ['region'] },
      loans: ['region'],
    });
    expect(
      sqlSchemaTree([
        { name: 'mart.accounts', columns: [{ name: 'id' }] },
        { name: 'public.accounts', columns: [{ name: 'code' }] },
      ]),
    ).toEqual({
      mart: { accounts: ['id'] },
      public: { accounts: ['code'] },
    });
    expect(sqlCompletionContext('select a from mart.loans limit 10')).toEqual({
      schema: 'mart',
      table: 'loans',
    });
    const schema = sqlSchemaTree([{ name: 'workspace.upload_bank', columns: [{ name: 'id' }] }]);
    expect(sqlCompletionDefaults(schema, sqlCompletionContext('SELECT * FROM u'))).toEqual({});
    expect(
      sqlCompletionDefaults(schema, sqlCompletionContext('SELECT * FROM workspace.upload_bank')),
    ).toEqual({
      defaultSchema: 'workspace',
      defaultTable: 'upload_bank',
    });
    const labels = (doc: string) => {
      const state = EditorState.create({ doc, extensions: [PostgreSQL.language] });
      const result = schemaCompletionSource({
        dialect: PostgreSQL,
        schema: schema as SQLNamespace,
      })(new CompletionContext(state, doc.length, false));
      return result && 'options' in result ? result.options.map((option) => option.label) : [];
    };
    expect(labels('SELECT * FROM u')).toContain('upload_bank');
    expect(labels('SELECT * FROM workspace.')).toContain('upload_bank');
    expect(labels('SELECT * FROM upload_bank.')).toContain('id');
  });

  it('groups the browser by schema under each source', () => {
    const tree = sqlBrowseTree([
      {
        id: 'b',
        schema: 'public',
        name: 'zones',
        qualified: 'public.zones',
        description: null,
        sourceId: 'src',
        sourceName: 'Kho',
      },
      {
        id: 'a',
        schema: 'mart',
        name: 'loans',
        qualified: 'mart.loans',
        description: null,
        sourceId: null,
        sourceName: null,
      },
      {
        id: 'c',
        schema: 'mart',
        name: 'accounts',
        qualified: 'mart.accounts',
        description: null,
        sourceId: null,
        sourceName: null,
      },
    ]);
    expect(tree.map((source) => source.name)).toEqual(['Bảng dẫn xuất', 'Kho']);
    expect(tree[0]?.schemas[0]).toMatchObject({
      name: 'mart',
      tables: [{ name: 'accounts' }, { name: 'loans' }],
    });
    expect(tree[1]?.schemas.map((schema) => schema.name)).toEqual(['public']);
  });

  it('flags a from-clause table the catalog does not have', () => {
    const tables = [{ name: 'mart.loans' }];
    expect(unknownSqlRelations('SELECT * FROM mart.loans', tables)).toEqual([]);
    expect(unknownSqlRelations('SELECT * FROM mart', tables)).toEqual([]);
    expect(unknownSqlRelations('WITH c AS (SELECT 1) SELECT * FROM c', tables)).toEqual([]);
    expect(unknownSqlRelations('SELECT * FROM secret.accounts', tables)).toEqual([
      { from: 14, to: 29, name: 'secret.accounts' },
    ]);
  });

  it('charts a Vietnamese decimal against the first text column', () => {
    expect(sqlNumber('0,0284')).toBe(0.0284);
    const chart = chartFromGrid(
      ['region', 'npl'],
      [
        ['ĐB sông Hồng', '0,0284'],
        ['Tây Nguyên', '0,0201'],
      ],
      'region',
      'npl',
    );
    expect(chart?.title).toBe('npl theo region');
    expect(chart?.data[0]).toEqual({ region: 'ĐB sông Hồng', npl: 0.0284 });
  });
});
