/**
 * Thêm / sửa một nguồn dữ liệu.
 *
 * The form is drawn from `GET /api/knowledge/sources/connectors`, not from a
 * list in this file: a protocol plugin added to the backend shows up here
 * without a frontend release. That is the same idea warehouse.data.ts already
 * used, moved to the side that actually knows what a plugin needs.
 *
 * "Thử" is not a convenience. Nobody types `VNM.GGXWDG.A` correctly from
 * memory — two guesses in a row were wrong while this was being built — so the
 * wizard fetches with the entered settings and shows what came back before
 * anything is saved. A steward who cannot see that will save something wrong
 * and find out from a schedule at 03:00.
 */
import React, { useEffect, useState } from 'react';
import { Loader2, CheckCircle2, AlertTriangle, XCircle, Play, X } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import { formatNumber } from '../../../lib/formatVN';

export interface ConnectorField {
  key: string;
  label: string;
  required?: boolean;
  placeholder?: string;
  help?: string;
  type?: 'json' | 'boolean';
  default?: unknown;
}

export interface ConnectorSchema {
  connector: string;
  label: string;
  description: string;
  series_key_label: string;
  series_key_placeholder: string;
  fields: ConnectorField[];
}

interface PreviewResult {
  ok: boolean;
  kind?: 'config' | 'response' | 'network';
  message?: string;
  release_date?: string;
  vintage_label?: string;
  count?: number;
  first_period?: string;
  last_period?: string;
  bytes?: number;
  warnings?: string[];
  sample?: Array<{ period: string; value: string }>;
}

// Which fault the steward is looking at. A wizard that says "failed" for all
// three sends people to debug the wrong thing: only the first is their form.
const FAULT: Record<string, string> = {
  config: 'Cấu hình chưa đúng — sửa các ô bên trên',
  response: 'Nguồn trả về thứ không đọc được — kiểm tra mã chuỗi và đường dẫn trích xuất',
  network: 'Không gọi được tới nguồn — mạng hoặc endpoint',
};

export function SourceWizard({ onClose, onSaved, sourceId }: {
  onClose: () => void;
  onSaved: () => void;
  /** Present when editing — the form loads that source and PATCHes it. */
  sourceId?: string;
}) {
  const [schemas, setSchemas] = useState<ConnectorSchema[]>([]);
  const [connector, setConnector] = useState<string>('');
  const [meta, setMeta] = useState({ code: '', title: '', publisher: '', schedule: '', licence: '' });
  const [config, setConfig] = useState<Record<string, string>>({});
  const [seriesKey, setSeriesKey] = useState('');
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [busy, setBusy] = useState<'preview' | 'save' | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const rows = await apiGet<ConnectorSchema[]>('/api/knowledge/sources/connectors');
      setSchemas(rows ?? []);
      if (!sourceId && rows?.length) setConnector(rows[0].connector);
      if (!sourceId) return;

      const s = await apiGet<Record<string, unknown>>(`/api/knowledge/sources/${sourceId}`);
      setConnector(String(s.connector ?? ''));
      setMeta({
        code: String(s.code ?? ''), title: String(s.title ?? ''),
        publisher: String(s.publisher ?? ''), schedule: String(s.schedule ?? ''),
        licence: String(s.licence ?? ''),
      });
      // Back to text for the inputs. Objects and arrays are re-serialised so a
      // steward edits the same JSON they originally pasted, not [object Object].
      const cfg = (s.config ?? {}) as Record<string, unknown>;
      setConfig(Object.fromEntries(Object.entries(cfg).map(([k, v]) => [
        k, typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v),
      ])));
    })();
  }, [sourceId]);

  const schema = schemas.find((s) => s.connector === connector);

  // Text in, typed value out: `rows_path` is [1] and `drop_projections` is a
  // boolean, and the backend rejects a string where it wants either.
  const typedConfig = (): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    for (const field of schema?.fields ?? []) {
      const raw = config[field.key];
      if (raw === undefined || raw === '') continue;
      if (field.type === 'json') {
        try { out[field.key] = JSON.parse(raw); } catch { out[field.key] = raw; }
      } else if (field.type === 'boolean') {
        out[field.key] = raw === 'true';
      } else {
        out[field.key] = raw;
      }
    }
    return out;
  };

  const runPreview = async () => {
    setBusy('preview'); setError(null);
    try {
      const result = await apiMutate<PreviewResult>('POST', '/api/knowledge/sources/preview', {
        connector, config: typedConfig(), series_key: seriesKey,
      });
      setPreview(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Thử không thành công');
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy('save'); setError(null);
    try {
      const body = {
        title: meta.title, publisher: meta.publisher || null,
        connector, config: typedConfig(),
        schedule: meta.schedule || null, licence: meta.licence || null,
      };
      // PATCH when editing: `code` is the source's identity and is not sent, so
      // renaming it is a deliberate act rather than a side effect of saving.
      if (sourceId) {
        await apiMutate('PATCH', `/api/knowledge/sources/${sourceId}`, body);
      } else {
        await apiMutate('POST', '/api/knowledge/sources', { ...body, code: meta.code });
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Lưu không thành công');
    } finally {
      setBusy(null);
    }
  };

  const missing = (schema?.fields ?? [])
    .filter((f) => f.required && !config[f.key])
    .map((f) => f.label);
  const canSave = !!meta.code && !!meta.title && missing.length === 0;

  return (
    <div className="fixed inset-0 z-50 bg-black/30 flex items-start justify-center overflow-auto py-10">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-[#EFEFEF]">
          <h3 className="text-[14px] font-semibold text-[#111111]">
            {sourceId ? 'Sửa nguồn dữ liệu' : 'Thêm nguồn dữ liệu'}
          </h3>
          <button onClick={onClose} className="text-[#8A8A8A] hover:text-[#111111]">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {error && <div className="px-3 py-2 rounded bg-red-50 text-red-700 text-[12px]">{error}</div>}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-medium text-[#5F5F5F] mb-1">Mã nguồn *</label>
              <input
                value={meta.code}
                placeholder="intl-bis-xru"
                disabled={!!sourceId}
                onChange={(e) => setMeta({ ...meta, code: e.target.value })}
                className="w-full px-2.5 py-1.5 border border-[#E0E0E0] rounded text-[12px] disabled:bg-[#F5F5F5] disabled:text-[#8A8A8A]"
              />
            </div>
            <Field label="Tên *" value={meta.title} placeholder="BIS Exchange Rates"
                   onChange={(v) => setMeta({ ...meta, title: v })} />
            <Field label="Cơ quan công bố" value={meta.publisher} placeholder="Bank for International Settlements"
                   onChange={(v) => setMeta({ ...meta, publisher: v })} />
            <Field label="Lịch" value={meta.schedule} placeholder="monthly"
                   onChange={(v) => setMeta({ ...meta, schedule: v })} />
          </div>

          <div>
            <label className="block text-[11px] font-medium text-[#5F5F5F] mb-1">Giao thức</label>
            <select
              value={connector}
              onChange={(e) => { setConnector(e.target.value); setConfig({}); setPreview(null); }}
              className="w-full px-2.5 py-1.5 border border-[#E0E0E0] rounded text-[12px]"
            >
              {schemas.map((s) => <option key={s.connector} value={s.connector}>{s.label}</option>)}
            </select>
            {schema && <p className="text-[11px] text-[#8A8A8A] mt-1">{schema.description}</p>}
          </div>

          {schema?.fields.map((field) => (
            <div key={field.key}>
              <label className="block text-[11px] font-medium text-[#5F5F5F] mb-1">
                {field.label}{field.required && ' *'}
              </label>
              {field.type === 'boolean' ? (
                <select
                  value={config[field.key] ?? String(field.default ?? false)}
                  onChange={(e) => setConfig({ ...config, [field.key]: e.target.value })}
                  className="w-full px-2.5 py-1.5 border border-[#E0E0E0] rounded text-[12px]"
                >
                  <option value="true">Có</option>
                  <option value="false">Không</option>
                </select>
              ) : (
                <input
                  value={config[field.key] ?? ''}
                  placeholder={field.placeholder}
                  onChange={(e) => setConfig({ ...config, [field.key]: e.target.value })}
                  className={cn('w-full px-2.5 py-1.5 border rounded text-[12px]',
                    field.type === 'json' && 'font-mono text-[11px]',
                    field.required && !config[field.key] ? 'border-amber-300' : 'border-[#E0E0E0]')}
                />
              )}
              {field.help && <p className="text-[10px] text-[#8A8A8A] mt-0.5">{field.help}</p>}
            </div>
          ))}

          <div className="border-t border-[#EFEFEF] pt-3">
            <label className="block text-[11px] font-medium text-[#5F5F5F] mb-1">
              {schema?.series_key_label ?? 'Mã chuỗi'} — để thử
            </label>
            <div className="flex gap-2">
              <input
                value={seriesKey}
                placeholder={schema?.series_key_placeholder}
                onChange={(e) => setSeriesKey(e.target.value)}
                className="flex-1 px-2.5 py-1.5 border border-[#E0E0E0] rounded text-[12px] font-mono"
              />
              <button
                onClick={() => void runPreview()}
                disabled={!seriesKey || busy !== null}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-[#E0E0E0] text-[12px] hover:bg-[#F7F7F7] disabled:opacity-50"
              >
                {busy === 'preview' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                Thử
              </button>
            </div>

            {preview && !preview.ok && (
              <div className="mt-2 px-3 py-2 rounded bg-red-50 text-[11px]">
                <div className="flex items-center gap-1.5 text-red-700 font-medium">
                  <XCircle className="w-3.5 h-3.5" />
                  {FAULT[preview.kind ?? ''] ?? 'Không thành công'}
                </div>
                <div className="text-red-600 mt-1 font-mono">{preview.message}</div>
              </div>
            )}

            {preview?.ok && (
              <div className="mt-2 px-3 py-2 rounded bg-emerald-50 text-[11px]">
                <div className="flex items-center gap-1.5 text-emerald-700 font-medium">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  {formatNumber(preview.count ?? 0)} kỳ · {preview.first_period}–{preview.last_period}
                  {' · '}công bố {preview.release_date} · {formatNumber(preview.bytes ?? 0)} bytes
                </div>
                <table className="mt-1.5 text-[11px] font-mono">
                  <tbody>
                    {preview.sample?.map((s) => (
                      <tr key={s.period}>
                        <td className="pr-4 text-[#5F5F5F]">{s.period}</td>
                        <td>{s.value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!!preview.warnings?.length && (
                  <div className="mt-1.5 flex items-start gap-1.5 text-amber-700">
                    <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                    <span>{preview.warnings[0]}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between px-5 py-3 border-t border-[#EFEFEF]">
          <span className="text-[11px] text-[#8A8A8A]">
            {missing.length ? `Còn thiếu: ${missing.join(', ')}` : 'Lưu nguồn rồi thêm chuỗi ở màn danh mục'}
          </span>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-3 py-1.5 rounded border border-[#E0E0E0] text-[12px]">Huỷ</button>
            <button
              onClick={() => void save()}
              disabled={!canSave || busy !== null}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-[#111111] text-white text-[12px] disabled:opacity-40"
            >
              {busy === 'save' && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {sourceId ? 'Lưu thay đổi' : 'Lưu nguồn'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, placeholder, onChange }: {
  label: string; value: string; placeholder?: string; onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="block text-[11px] font-medium text-[#5F5F5F] mb-1">{label}</label>
      <input
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-2.5 py-1.5 border border-[#E0E0E0] rounded text-[12px]"
      />
    </div>
  );
}
