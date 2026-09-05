import React, { useEffect, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, Plus, Trash2 } from 'lucide-react';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import { KnowledgeDocument } from '../../../types';

const COLUMN_TYPES = ['text', 'integer', 'numeric', 'boolean', 'date', 'timestamp'] as const;

interface ColumnState { name: string; type: string; nullable: boolean; source_header?: string; }

interface StructuredJob {
  job_id: string;
  status: 'proposing' | 'proposed' | 'approved' | 'rejected' | 'failed';
  llm_proposed_schema: { table_name: string; description?: string; columns: ColumnState[] } | null;
  sample_rows: { rows: Record<string, any>[] } | null;
  created_table_name: string | null;
  error_message: string | null;
}

export const StructuredTableReview = ({
  document, onClose, onDone,
}: {
  document: KnowledgeDocument;
  onClose: () => void;
  onDone: () => void;
}) => {
  const [job, setJob] = useState<StructuredJob | null>(null);
  const [tableName, setTableName] = useState('');
  const [columns, setColumns] = useState<ColumnState[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<number | null>(null);

  const applyProposal = (j: StructuredJob) => {
    if (j.llm_proposed_schema) {
      setTableName(j.llm_proposed_schema.table_name);
      setColumns(j.llm_proposed_schema.columns.map(c => ({ ...c })));
    }
  };

  const fetchJob = async (): Promise<StructuredJob | null> => {
    try {
      return await apiGet<StructuredJob>(`/api/pipeline/structured/${document.data_id}`);
    } catch {
      return null;
    }
  };

  useEffect(() => {
    let cancelled = false;

    const startOrResume = async () => {
      let current = await fetchJob();
      if (!current) {
        try {
          await apiMutate('POST', `/api/pipeline/structured/${document.data_id}/propose`);
        } catch (e) {
          if (!cancelled) setErrorMsg(`Could not start schema proposal: ${(e as Error).message}`);
          return;
        }
        current = await fetchJob();
      }
      if (cancelled || !current) return;
      setJob(current);
      applyProposal(current);

      if (current.status === 'proposing') {
        pollRef.current = window.setInterval(async () => {
          const next = await fetchJob();
          if (!next || cancelled) return;
          if (next.status !== 'proposing') {
            if (pollRef.current) window.clearInterval(pollRef.current);
            setJob(next);
            applyProposal(next);
          }
        }, 2000);
      }
    };

    startOrResume();
    return () => {
      cancelled = true;
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document.data_id]);

  const updateColumn = (idx: number, patch: Partial<ColumnState>) => {
    setColumns(prev => prev.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  };

  const removeColumn = (idx: number) => setColumns(prev => prev.filter((_, i) => i !== idx));
  const addColumn = () => setColumns(prev => [...prev, { name: '', type: 'text', nullable: true }]);

  const handleApprove = async () => {
    setErrorMsg(null);
    if (!tableName.trim()) { setErrorMsg('Table name is required.'); return; }
    if (columns.length === 0) { setErrorMsg('At least one column is required.'); return; }
    for (const c of columns) {
      if (!c.name.trim()) { setErrorMsg('Every column needs a name.'); return; }
    }
    setSubmitting(true);
    try {
      const result = await apiMutate<{ table_name: string; row_count: number; coercion_warnings: number }>(
        'POST',
        `/api/pipeline/structured/${document.data_id}/approve`,
        { schema: { table_name: tableName.trim(), columns: columns.map(c => ({ name: c.name.trim(), type: c.type, nullable: c.nullable, source_header: c.source_header || null })) } },
      );
      setJob(prev => prev ? { ...prev, status: 'approved', created_table_name: result.table_name } : prev);
      window.setTimeout(onDone, 1200);
    } catch (e) {
      setErrorMsg(`Could not create table: ${(e as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleReject = async () => {
    setSubmitting(true);
    try {
      await apiMutate('POST', `/api/pipeline/structured/${document.data_id}/reject`);
      onDone();
    } catch (e) {
      setErrorMsg(`Could not cancel: ${(e as Error).message}`);
      setSubmitting(false);
    }
  };

  const sampleRows = job?.sample_rows?.rows ?? [];

  return (
    <div className="flex flex-col h-full bg-[#FCFBF7] text-[#111111]">
      <div className="flex-1 overflow-y-auto p-6 md:p-8 custom-scrollbar space-y-5">
        {(!job || job.status === 'proposing') && (
          <div className="flex flex-col items-center justify-center py-16 text-center gap-3">
            <div className="w-10 h-10 rounded-full border-4 border-[#BFA66A] border-t-[#B88719] animate-spin" />
            <p className="text-xs font-bold text-[#8A5A00] uppercase tracking-wide">Analyzing file &amp; proposing schema…</p>
            <p className="text-[10px] text-[#5A5A5A] max-w-xs">Docling reads the table, then an LLM proposes column names and types. Usually takes under a minute.</p>
          </div>
        )}

        {job?.status === 'failed' && (
          <div className="px-4 py-3 bg-[#FAF1F1] border border-[#C94A4A] rounded-xl flex items-start gap-3 text-xs text-[#9F1D1D]">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <div>
              <p className="font-bold">Could not propose a schema</p>
              <p className="mt-0.5">{job.error_message || 'No table was detected in this file.'}</p>
              <p className="mt-1.5 text-[#5A5A5A]">You can still add this file to the knowledge base normally from the Inventory list (Process to Silver).</p>
            </div>
          </div>
        )}

        {job?.status === 'approved' && (
          <div className="px-4 py-3 bg-[#EAF7EE] border border-[#6B8E23] rounded-xl flex items-center gap-3 text-xs text-[#2F4F0B]">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            <span>Table <span className="font-mono font-bold">{job.created_table_name}</span> created.</span>
          </div>
        )}

        {job?.status === 'proposed' && (
          <>
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-black uppercase tracking-widest text-[#111111]">Table Name</label>
              <input
                type="text"
                value={tableName}
                onChange={(e) => setTableName(e.target.value)}
                className="field-input text-xs font-mono"
              />
            </div>

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <label className="text-[10px] font-black uppercase tracking-widest text-[#111111]">Columns</label>
                <button type="button" onClick={addColumn} className="btn-secondary btn-sm inline-flex items-center gap-1">
                  <Plus className="w-3 h-3" /> Add column
                </button>
              </div>
              <div className="border-2 border-[#BFA66A] rounded-2xl overflow-hidden bg-white">
                <table className="w-full text-xs">
                  <thead className="bg-[#FFFDF6] border-b border-[#BFA66A]">
                    <tr>
                      <th className="text-left font-black uppercase tracking-wider text-[10px] p-2.5">Column Name</th>
                      <th className="text-left font-black uppercase tracking-wider text-[10px] p-2.5">Type</th>
                      <th className="text-center font-black uppercase tracking-wider text-[10px] p-2.5">Nullable</th>
                      <th className="text-left font-black uppercase tracking-wider text-[10px] p-2.5">Source Header</th>
                      <th className="p-2.5" />
                    </tr>
                  </thead>
                  <tbody>
                    {columns.map((col, idx) => (
                      <tr key={idx} className="border-b border-[#F0E8D0] last:border-0">
                        <td className="p-2">
                          <input
                            value={col.name}
                            onChange={(e) => updateColumn(idx, { name: e.target.value })}
                            className="field-input text-xs font-mono py-1.5"
                          />
                        </td>
                        <td className="p-2">
                          <select
                            value={col.type}
                            onChange={(e) => updateColumn(idx, { type: e.target.value })}
                            className="field-input text-xs py-1.5"
                          >
                            {COLUMN_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                          </select>
                        </td>
                        <td className="p-2 text-center">
                          <input
                            type="checkbox"
                            checked={col.nullable}
                            onChange={(e) => updateColumn(idx, { nullable: e.target.checked })}
                            className="w-3.5 h-3.5"
                          />
                        </td>
                        <td className="p-2 text-[10px] text-[#5A5A5A] font-mono">{col.source_header || '—'}</td>
                        <td className="p-2">
                          <button type="button" onClick={() => removeColumn(idx)} className="p-1 text-red-500 hover:bg-red-50 rounded">
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {sampleRows.length > 0 && (
              <div className="flex flex-col gap-2">
                <label className="text-[10px] font-black uppercase tracking-widest text-[#111111]">
                  Preview ({sampleRows.length} of the parsed rows)
                </label>
                <div className="border-2 border-[#BFA66A] rounded-2xl overflow-auto bg-white max-h-64">
                  <table className="w-full text-[10px] font-mono">
                    <thead className="bg-[#FFFDF6] border-b border-[#BFA66A] sticky top-0">
                      <tr>
                        {Object.keys(sampleRows[0]).map(h => (
                          <th key={h} className="text-left font-bold p-2 whitespace-nowrap">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sampleRows.map((row, i) => (
                        <tr key={i} className="border-b border-[#F0E8D0] last:border-0">
                          {Object.keys(sampleRows[0]).map(h => (
                            <td key={h} className="p-2 whitespace-nowrap text-[#5A5A5A]">{String(row[h] ?? '')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}

        {errorMsg && (
          <div className="px-4 py-2.5 bg-[#FAF1F1] border border-[#C94A4A] rounded-xl flex items-center gap-3 text-xs text-[#9F1D1D]">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}
      </div>

      <div className="p-6 border-t border-[#BFA66A] bg-[#FAF6EA] flex justify-end gap-2 shrink-0">
        <button onClick={onClose} className="btn-secondary h-10 px-5 text-xs">
          {job?.status === 'approved' ? 'Close' : 'Cancel'}
        </button>
        {job?.status === 'proposed' && (
          <>
            <button onClick={handleReject} disabled={submitting} className="btn-secondary h-10 px-5 text-xs disabled:opacity-60">
              Reject
            </button>
            <button onClick={handleApprove} disabled={submitting} className="btn-gold h-10 px-6 font-black uppercase disabled:opacity-60">
              {submitting ? 'Creating…' : 'Create Table'}
            </button>
          </>
        )}
      </div>
    </div>
  );
};
