// Shared field groups for a source's REST/SDMX auth params, a web-crawl's
// recursive-crawl options, and the Manual/Auto promotion control — used
// identically by SourceRegistry's Add/Edit forms and by IngestionWizard's
// "From Feed-or-API" / Web Portal methods, so all four places read one
// definition of what these fields look like and how they behave.

export interface RestSdmxFieldsValue {
  queryParamsText: string; openbaoPath: string; authStyle: string; authParamName: string;
}

export function RestSdmxFields({
  value, onChange,
}: { value: RestSdmxFieldsValue; onChange: (next: RestSdmxFieldsValue) => void }) {
  const patch = (next: Partial<RestSdmxFieldsValue>) => onChange({ ...value, ...next });
  return (
    <>
      <div>
        <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Query Params (one "key=value" per line)</label>
        <textarea
          className="ui-input font-mono text-xs h-20 w-full px-3 py-2"
          value={value.queryParamsText}
          onChange={e => patch({ queryParamsText: e.target.value })}
          placeholder="frequency=M&#10;start_period=2020-01"
        />
      </div>
      <div>
        <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">OpenBao Path (optional — auth credential)</label>
        <input
          className="ui-input w-full text-xs px-3 py-2 font-mono"
          value={value.openbaoPath}
          onChange={e => patch({ openbaoPath: e.target.value })}
          placeholder="tenants/<tenant_id>/<key>"
        />
      </div>
      {value.openbaoPath && (
        <div className="flex items-end gap-3">
          <div className="flex-1">
            <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Auth Style</label>
            <select
              className="ui-input w-full text-xs px-3 py-2"
              value={value.authStyle}
              onChange={e => patch({ authStyle: e.target.value })}
            >
              <option value="bearer">Bearer header</option>
              <option value="query_param">Query param (e.g. api_key=)</option>
            </select>
          </div>
          {value.authStyle === 'query_param' && (
            <div className="flex-1">
              <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Param Name</label>
              <input
                className="ui-input w-full text-xs px-3 py-2 font-mono"
                value={value.authParamName}
                onChange={e => patch({ authParamName: e.target.value })}
                placeholder="api_key"
              />
            </div>
          )}
        </div>
      )}
    </>
  );
}

export interface RecursiveCrawlFieldsValue {
  recursive: boolean; maxDepth: number; sameDomainOnly: boolean;
}

export function RecursiveCrawlFields({
  value, onChange,
}: { value: RecursiveCrawlFieldsValue; onChange: (next: RecursiveCrawlFieldsValue) => void }) {
  const patch = (next: Partial<RecursiveCrawlFieldsValue>) => onChange({ ...value, ...next });
  return (
    <div className="flex items-center gap-4 flex-wrap">
      <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
        <input type="checkbox" checked={value.recursive} onChange={e => patch({ recursive: e.target.checked })} />
        Follow links (recursive crawl)
      </label>
      {value.recursive && (
        <>
          <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
            Max depth
            <input
              type="number" min={1} max={5}
              className="ui-input w-16 text-xs px-2 py-1 font-mono"
              value={value.maxDepth}
              onChange={e => patch({ maxDepth: parseInt(e.target.value, 10) || 1 })}
            />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-[#3F3F3F]">
            <input type="checkbox" checked={value.sameDomainOnly} onChange={e => patch({ sameDomainOnly: e.target.checked })} />
            Same domain only
          </label>
        </>
      )}
    </div>
  );
}

export function PromotionModeControl({
  value, effective, onChange, defaultHint,
}: {
  value: 'manual' | 'auto' | null; effective: 'manual' | 'auto';
  onChange: (mode: 'manual' | 'auto') => void; defaultHint?: string;
}) {
  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Promotion to Gold</label>
      <div className="flex items-center gap-1.5">
        {(['manual', 'auto'] as const).map(mode => (
          <button
            key={mode}
            type="button"
            onClick={() => onChange(mode)}
            className={`px-3 py-1.5 rounded-full text-[11px] font-bold border transition-colors ${
              effective === mode ? 'bg-[#B88719] border-[#B88719] text-white' : 'bg-white border-[#BFA66A] text-[#5F5F5F] hover:bg-[#FFF9E8]'
            }`}
          >
            {mode === 'manual' ? 'Manual' : 'Auto (straight to Gold)'}
          </button>
        ))}
        {value === null && defaultHint && (
          <span className="text-[10px] font-mono text-[#8A8A8A]">({defaultHint})</span>
        )}
      </div>
    </div>
  );
}
