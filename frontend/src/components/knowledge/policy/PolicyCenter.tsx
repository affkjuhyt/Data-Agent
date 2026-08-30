import React, { useState, useEffect } from 'react';
import {
  Filter,
  Cpu,
  Plus,
  Edit2,
  Trash2,
  Lock,
  Search,
  Save,
  X,
  Sparkles,
  Tag,
  AlignLeft,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { DetailDrawer } from '../../shared/DetailDrawer';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import { PagedResponse } from '../../../types';
import {
  FilterPolicyType, FilterPolicy, BASE_EXTRACTION_POLICY,
} from './policy.data';

// Backend may return `content` as a structured value (e.g. {type, value} or an
// array of rule objects) rather than a plain string. Coerce defensively so the
// edit form never receives a non-string — calling .trim() on an object crashes
// the whole drawer.
const contentToText = (content: unknown): string =>
  typeof content === 'string' ? content : content == null ? '' : JSON.stringify(content);

const contentToWords = (content: unknown): string[] => {
  let raw: unknown = content;
  if (typeof content === 'string') {
    try { raw = JSON.parse(content); }
    catch { return content.split(',').map(s => s.trim()).filter(Boolean); }
  }
  if (!Array.isArray(raw)) return [];
  return raw.map(w =>
    typeof w === 'string'
      ? w
      : w && typeof w === 'object' && 'value' in (w as any)
        ? String((w as any).value)
        : JSON.stringify(w),
  );
};

export const PolicyCenter = () => {
  const [filterPolicies, setFilterPolicies] = useState<FilterPolicy[]>([]);
  const [policiesPage, setPoliciesPage] = useState(1);
  const [policiesHasNext, setPoliciesHasNext] = useState(false);
  const [policiesLoading, setPoliciesLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  // Drawer state
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingPolicy, setEditingPolicy] = useState<FilterPolicy | null>(null);

  // Form fields
  const [formName, setFormName] = useState('');
  const [formType, setFormType] = useState<FilterPolicyType>('natural_language');
  const [formContent, setFormContent] = useState('');
  const [formWordInput, setFormWordInput] = useState('');
  const [formWords, setFormWords] = useState<string[]>([]);

  // Delete confirmation
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Extraction custom policy
  const [customExtraction, setCustomExtraction] = useState('');
  const [editingExtraction, setEditingExtraction] = useState(false);
  const [customExtractionDraft, setCustomExtractionDraft] = useState('');

  const fetchPolicies = (page: number) => {
    setPoliciesLoading(true);
    apiGet<PagedResponse<FilterPolicy> | FilterPolicy[]>('/api/knowledge/policies/filtering', { page })
      .then(data => {
        if (Array.isArray(data)) {
          setFilterPolicies(data);
          setPoliciesHasNext(false);
        } else {
          setFilterPolicies(data.items ?? []);
          setPoliciesHasNext(data.has_next ?? false);
        }
      })
      .catch(() => { setFilterPolicies([]); })
      .finally(() => setPoliciesLoading(false));
  };

  useEffect(() => {
    fetchPolicies(policiesPage);
  }, [policiesPage]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    apiGet<{ base: string; custom: string }>('/api/knowledge/policies/extraction')
      .then(data => setCustomExtraction(data.custom ?? ''))
      .catch(() => {});
  }, []);

  const openAddDrawer = () => {
    setEditingPolicy(null);
    setFormName('');
    setFormType('natural_language');
    setFormContent('');
    setFormWords([]);
    setFormWordInput('');
    setSaveError(null);
    setDrawerOpen(true);
  };

  const openEditDrawer = (policy: FilterPolicy) => {
    setEditingPolicy(policy);
    setFormName(policy.name);
    setFormType(policy.type);
    if (policy.type === 'natural_language') {
      setFormContent(contentToText(policy.content));
      setFormWords([]);
    } else {
      setFormContent('');
      setFormWords(contentToWords(policy.content));
    }
    setFormWordInput('');
    setSaveError(null);
    setDrawerOpen(true);
  };

  const handleSavePolicy = async () => {
    const content =
      formType === 'natural_language' ? formContent : JSON.stringify(formWords);

    setSaving(true);
    setSaveError(null);
    try {
      if (editingPolicy) {
        await apiMutate('PUT', `/api/knowledge/policies/filtering/${editingPolicy.id}`, { name: formName, type: formType, content });
      } else {
        await apiMutate('POST', '/api/knowledge/policies/filtering', { name: formName, type: formType, content });
      }
      setDrawerOpen(false);
      setPoliciesPage(1);
      fetchPolicies(1);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save policy');
    } finally {
      setSaving(false);
    }
  };

  const handleToggleActive = (id: string) => {
    const current = filterPolicies.find(p => p.id === id);
    if (!current) return;
    const newActive = !current.active;
    setFilterPolicies(prev => prev.map(p => p.id === id ? { ...p, active: newActive } : p));
    apiMutate('PUT', `/api/knowledge/policies/filtering/${id}`, { active: newActive })
      .catch(() => {
        // Revert optimistic update on failure
        setFilterPolicies(prev => prev.map(p => p.id === id ? { ...p, active: current.active } : p));
      });
  };

  const handleDelete = (id: string) => {
    setFilterPolicies(prev => prev.filter(p => p.id !== id));
    apiMutate('DELETE', `/api/knowledge/policies/filtering/${id}`).catch(() => {});
    setDeletingId(null);
  };

  const handleAddWord = () => {
    const w = formWordInput.trim();
    if (w && !formWords.includes(w)) setFormWords(prev => [...prev, w]);
    setFormWordInput('');
  };

  const filteredPolicies = filterPolicies.filter(
    p =>
      p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.type.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (p.added_by ?? '').toLowerCase().includes(searchQuery.toLowerCase())
  );

  const isFormValid =
    formName.trim().length > 0 &&
    (formType === 'natural_language' ? formContent.trim().length > 0 : formWords.length > 0);

  return (
    <div className="space-y-10 animate-in fade-in duration-500 text-[#111111]">

      {/* ── ADD / EDIT DRAWER ── */}
      <DetailDrawer
        isOpen={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={editingPolicy ? `Edit · ${editingPolicy.id}` : 'Add Filtering Policy'}
        subtitle={editingPolicy ? `Modify "${editingPolicy.name}"` : 'Bronze → Silver transition filter'}
        icon={Filter}
        size="standard"
        footer={
          <div className="flex flex-col gap-2 w-full">
            {saveError && (
              <p className="text-[11px] text-red-600 font-mono bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                {saveError}
              </p>
            )}
            <div className="flex justify-end gap-2.5">
              <button
                onClick={() => setDrawerOpen(false)}
                disabled={saving}
                className="btn-secondary"
              >
                Cancel
              </button>
              <button
                onClick={handleSavePolicy}
                disabled={!isFormValid || saving}
                className={cn(
                  'px-5 py-2 rounded-xl text-xs font-black uppercase tracking-wider transition-all flex items-center gap-1.5 cursor-pointer',
                  isFormValid && !saving
                    ? 'bg-[#B88719] text-white hover:bg-[#8A5A00]'
                    : 'bg-[#FFF9E8] text-[#5F5F5F] border border-[#BFA66A] cursor-not-allowed'
                )}
              >
                {saving ? (
                  <>
                    <span className="w-3.5 h-3.5 rounded-full border-2 border-[#A8945E] border-t-[#B88719] animate-spin" />
                    <span>Saving...</span>
                  </>
                ) : (
                  <>
                    <Save className="w-3.5 h-3.5" />
                    {editingPolicy ? 'Save Changes' : 'Create Policy'}
                  </>
                )}
              </button>
            </div>
          </div>
        }
      >
        <div className="p-6 space-y-6">
          {/* Name */}
          <div className="space-y-1.5">
            <label className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono block">
              Policy Name
            </label>
            <input
              type="text"
              value={formName}
              onChange={e => setFormName(e.target.value)}
              placeholder="e.g. Exclude PII Data"
              className="ui-input w-full px-4 py-2.5 text-xs font-mono"
            />
          </div>

          {/* Type selector */}
          <div className="space-y-1.5">
            <label className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono block">
              Policy Type
            </label>
            <div className="grid grid-cols-2 gap-3">
              {(
                [
                  {
                    value: 'natural_language',
                    icon: AlignLeft,
                    label: 'Natural Language',
                    desc: 'Prompt-style filter using LLM reasoning',
                  },
                  {
                    value: 'exact_word',
                    icon: Tag,
                    label: 'Exact Word',
                    desc: 'Keyword & phrase exact matching',
                  },
                ] as const
              ).map(({ value, icon: Icon, label, desc }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setFormType(value);
                    if (value === 'natural_language') setFormWords([]);
                    else setFormContent('');
                  }}
                  className={cn(
                    'p-3 rounded-xl border text-left transition-all flex flex-col gap-1 cursor-pointer',
                    formType === value
                      ? 'border-[#B88719] bg-[#FFFBF0] ring-1 ring-[#B88719]'
                      : 'border-[#BFA66A] bg-white hover:bg-[#FFF9E8]'
                  )}
                >
                  <span className="flex items-center gap-1.5 text-xs font-bold text-[#111111]">
                    <Icon className="w-3.5 h-3.5 text-[#B88719]" />
                    {label}
                  </span>
                  <span className="text-[10px] text-[#5F5F5F]">{desc}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Content area — changes by type */}
          {formType === 'natural_language' ? (
            <div className="space-y-1.5">
              <label className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono block">
                Filter Prompt
              </label>
              <textarea
                value={formContent}
                onChange={e => setFormContent(e.target.value)}
                placeholder="Describe what this filter should remove or exclude when promoting documents from Bronze to Silver..."
                rows={5}
                className="ui-input w-full px-4 py-3 text-xs font-mono leading-relaxed resize-none"
              />
            </div>
          ) : (
            <div className="space-y-2">
              <label className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono block">
                Words & Phrases
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={formWordInput}
                  onChange={e => setFormWordInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') { e.preventDefault(); handleAddWord(); }
                  }}
                  placeholder="Type a word or phrase and press Enter"
                  className="ui-input flex-1 px-4 py-2.5 text-xs font-mono"
                />
                <button
                  type="button"
                  onClick={handleAddWord}
                  className="px-3 py-2 bg-[#B88719] text-white rounded-xl text-xs font-bold hover:bg-[#8A5A00] transition-all cursor-pointer"
                >
                  Add
                </button>
              </div>
              {formWords.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {formWords.map((w, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center gap-1 px-2.5 py-1 bg-[#FFF9E8] border border-[#BFA66A] rounded-lg text-[11px] font-mono font-bold text-[#8A5A00]"
                    >
                      {w}
                      <button
                        type="button"
                        onClick={() => setFormWords(prev => prev.filter((_, j) => j !== i))}
                        className="text-[#8A5A00] hover:text-red-600 transition-colors cursor-pointer"
                      >
                        <X className="w-2.5 h-2.5" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </DetailDrawer>

      {/* ── SECTION 1: FILTERING POLICIES ── */}
      <section className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-1.5 text-[10px] font-black tracking-widest uppercase text-[#8A5A00] font-mono mb-1">
              <Filter className="w-3.5 h-3.5" />
              Bronze → Silver
            </div>
            <h2 className="text-xl font-display font-bold tracking-tight">Filtering Policies</h2>
            <p className="text-[#5F5F5F] text-sm mt-0.5 max-w-2xl">
              Rules applied to raw Bronze documents before promotion to the Silver normalized layer. Inactive policies are stored but not applied.
            </p>
          </div>
          <button onClick={openAddDrawer} className="btn-primary shrink-0">
            <Plus className="w-4 h-4" />
            Add Policy
          </button>
        </div>

        {/* Search bar */}
        <div className="relative group w-full sm:w-72">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8A5A00] opacity-65 group-focus-within:opacity-100 transition-opacity" />
          <input
            type="text"
            placeholder="Search policies..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="ui-input pl-12 pr-6 py-2.5 text-xs w-full font-mono"
          />
        </div>

        {/* Table */}
        <div className="content-card content-card--static overflow-hidden">
          <div className="p-4 bg-[#FFFDF8] border-b border-[#BFA66A] flex items-center justify-between">
            <span className="text-[10px] font-mono font-black text-[#5A4209] uppercase tracking-widest">
              Active Policy Registry
            </span>
            <span className="text-[9px] font-mono font-bold py-1 px-2 bg-[#FFF9E8] border border-[#BFA66A] rounded-lg text-[#3F3F3F]">
              KB.FilterPolicies ({filteredPolicies.length})
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-[#FFFDF8] text-[9px] font-black text-[#5A4209] uppercase tracking-widest border-b border-[#BFA66A]">
                  <th className="px-5 py-3">Policy Name</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Added By</th>
                  <th className="px-4 py-3">Added When</th>
                  <th className="px-4 py-3 text-center">Status</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#BFA66A]">
                {filteredPolicies.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-8 py-14 text-center text-[#5F5F5F] text-xs font-mono">
                      No filtering policies found.
                    </td>
                  </tr>
                ) : (
                  filteredPolicies.map(policy => (
                    <tr
                      key={policy.id}
                      className={cn(
                        'transition-all',
                        policy.active
                          ? 'hover:bg-[#FFF9E8]/30'
                          : 'bg-[#FFF9E8]/50 hover:bg-[#FFF9E8]'
                      )}
                    >
                      {/* Name */}
                      <td className="px-5 py-4">
                        <div
                          className={cn(
                            'font-bold text-xs truncate max-w-[200px]',
                            policy.active ? 'text-[#111111]' : 'text-[#5F5F5F]'
                          )}
                        >
                          {policy.name}
                        </div>
                        <div className="text-[9px] text-[#5F5F5F] font-mono mt-0.5">{policy.id}</div>
                      </td>

                      {/* Type */}
                      <td className="px-4 py-4">
                        {policy.type === 'natural_language' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-1 bg-blue-50 border border-blue-200 rounded-lg text-[10px] font-bold text-blue-700 font-mono whitespace-nowrap">
                            <AlignLeft className="w-2.5 h-2.5" />
                            Natural Language
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-1 bg-purple-50 border border-purple-200 rounded-lg text-[10px] font-bold text-purple-700 font-mono whitespace-nowrap">
                            <Tag className="w-2.5 h-2.5" />
                            Exact Word
                          </span>
                        )}
                      </td>

                      {/* Added By */}
                      <td className="px-4 py-4 text-xs text-[#3F3F3F] font-mono">{policy.added_by}</td>

                      {/* Added When */}
                      <td className="px-4 py-4 text-xs text-[#5F5F5F] font-mono">{policy.added_when}</td>

                      {/* Status toggle */}
                      <td className="px-4 py-4 text-center">
                        <button
                          onClick={() => handleToggleActive(policy.id)}
                          className={cn(
                            'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold border transition-all cursor-pointer whitespace-nowrap',
                            policy.active
                              ? 'bg-green-50 border-green-300 text-green-700 hover:bg-green-100'
                              : 'bg-[#FFF9E8] border-[#BFA66A] text-[#5F5F5F] hover:bg-[#F4E8C3]'
                          )}
                        >
                          <span
                            className={cn(
                              'w-1.5 h-1.5 rounded-full',
                              policy.active ? 'bg-green-500' : 'bg-slate-400'
                            )}
                          />
                          {policy.active ? 'Active' : 'Inactive'}
                        </button>
                      </td>

                      {/* Actions */}
                      <td className="px-4 py-4 text-right">
                        {deletingId === policy.id ? (
                          <div className="flex items-center justify-end gap-1.5">
                            <span className="text-[10px] text-red-600 font-bold font-mono">Delete?</span>
                            <button
                              onClick={() => handleDelete(policy.id)}
                              className="px-2 py-1 bg-red-600 text-white rounded-lg text-[10px] font-bold hover:bg-red-700 transition-all cursor-pointer"
                            >
                              Yes
                            </button>
                            <button
                              onClick={() => setDeletingId(null)}
                              className="btn-ghost btn-sm"
                            >
                              No
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center justify-end gap-1.5">
                            <button
                              onClick={() => openEditDrawer(policy)}
                              className="p-1.5 rounded-lg bg-[#FFF9E8] border border-[#BFA66A] hover:bg-[#B88719] hover:text-white text-[#8A5A00] transition-all cursor-pointer"
                              title="Edit policy"
                            >
                              <Edit2 className="w-3.5 h-3.5" />
                            </button>
                            <button
                              onClick={() => setDeletingId(policy.id)}
                              className="p-1.5 rounded-lg bg-red-50 border border-red-200 hover:bg-red-600 hover:text-white text-red-600 transition-all cursor-pointer"
                              title="Delete policy"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
            {(policiesPage > 1 || policiesHasNext) && (
              <div className="flex items-center justify-between px-5 py-3 border-t border-[#BFA66A] bg-[#FFFDF8] rounded-b-2xl">
                <span className="text-[10px] font-mono text-[#5F5F5F]">Page {policiesPage}</span>
                <div className="flex gap-1.5">
                  <button disabled={policiesPage === 1 || policiesLoading} onClick={() => setPoliciesPage(p => Math.max(1, p - 1))}
                    className={cn("px-2.5 py-1 rounded text-[10px] font-bold border border-[#BFA66A] bg-white text-[#8A5A00]", (policiesPage === 1 || policiesLoading) && "opacity-40 cursor-not-allowed")}>
                    ‹ Prev
                  </button>
                  <button disabled={!policiesHasNext || policiesLoading} onClick={() => setPoliciesPage(p => p + 1)}
                    className={cn("px-2.5 py-1 rounded text-[10px] font-bold border border-[#BFA66A] bg-white text-[#8A5A00]", (!policiesHasNext || policiesLoading) && "opacity-40 cursor-not-allowed")}>
                    Next ›
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ── DIVIDER ── */}
      <div className="border-t border-[#BFA66A]" />

      {/* ── SECTION 2: EXTRACTION POLICIES ── */}
      <section className="space-y-4">
        <div>
          <div className="flex items-center gap-1.5 text-[10px] font-black tracking-widest uppercase text-[#8A5A00] font-mono mb-1">
            <Cpu className="w-3.5 h-3.5" />
            Silver → Graph / Qdrant
          </div>
          <h2 className="text-xl font-display font-bold tracking-tight">Extraction Policies</h2>
          <p className="text-[#5F5F5F] text-sm mt-0.5 max-w-2xl">
            Controls entity and relationship extraction from Silver layer documents during graph synthesis and Qdrant indexing. The base policy is system-managed and immutable.
          </p>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">

          {/* Card 1 — Base Policy (Locked) */}
          <div className="border border-[#BFA66A] rounded-3xl overflow-hidden shadow-sm flex flex-col">
            <div className="px-5 py-4 bg-[#111111] flex items-center justify-between shrink-0">
              <div className="flex items-center gap-2.5">
                <div className="p-1.5 bg-[#B88719] rounded-lg border border-[#8A5A00]">
                  <Cpu className="w-4 h-4 text-white" />
                </div>
                <div>
                  <h3 className="text-xs font-bold text-[#EADBB8] font-mono uppercase tracking-widest">
                    Base Extraction Policy
                  </h3>
                  <p className="text-[10px] text-[#5F5F5F]">System-managed · Immutable</p>
                </div>
              </div>
              <div className="flex items-center gap-1.5 px-2.5 py-1 bg-white/5 border border-white/20 rounded-lg">
                <Lock className="w-3 h-3 text-[#BFA66A]" />
                <span className="text-[9px] font-mono font-bold text-[#BFA66A] uppercase tracking-wider">Locked</span>
              </div>
            </div>
            <div className="p-5 bg-[#FAFAF8] flex-1">
              <pre className="text-[11px] font-mono text-[#2A2A2A] leading-relaxed whitespace-pre-wrap bg-[#F2F1EC] border border-[#BFA66A] rounded-xl p-4 select-none pointer-events-none">
                {BASE_EXTRACTION_POLICY}
              </pre>
            </div>
          </div>

          {/* Card 2 — Custom Extraction (Editable) */}
          <div className="border border-[#BFA66A] rounded-3xl overflow-hidden shadow-sm flex flex-col">
            <div className="px-5 py-4 bg-[#FFFDF8] border-b border-[#BFA66A] flex items-center justify-between shrink-0">
              <div className="flex items-center gap-2.5">
                <div className="p-1.5 bg-[#FFF9E8] border border-[#BFA66A] rounded-lg">
                  <Sparkles className="w-4 h-4 text-[#B88719]" />
                </div>
                <div>
                  <h3 className="text-xs font-bold text-[#2A2A2A] font-mono uppercase tracking-widest">
                    Custom Extraction Addendum
                  </h3>
                  <p className="text-[10px] text-[#5F5F5F]">Appended to base policy at runtime · Editable</p>
                </div>
              </div>
              {!editingExtraction && (
                <button
                  onClick={() => {
                    setCustomExtractionDraft(customExtraction);
                    setEditingExtraction(true);
                  }}
                  className="btn-ghost btn-sm"
                >
                  <Edit2 className="w-3 h-3" />
                  Edit
                </button>
              )}
            </div>

            <div className="p-5 flex-1 flex flex-col">
              {editingExtraction ? (
                <div className="space-y-3 flex flex-col flex-1">
                  <textarea
                    value={customExtractionDraft}
                    onChange={e => setCustomExtractionDraft(e.target.value)}
                    placeholder={`Add custom extraction directives to extend the base policy, e.g.:\n\nAlso extract:\n• Contract clauses (CLAUSE): legal provisions and obligations\n• Product codes (SKU): alphanumeric identifiers prefixed with 'SKU-'\n\nAdditional relationships:\n• AUTHORIZED_BY, SUPERSEDED_BY, PENDING_REVIEW`}
                    className="ui-input flex-1 w-full min-h-[200px] px-4 py-3 text-xs font-mono leading-relaxed resize-none"
                  />
                  <div className="flex justify-end gap-2">
                    <button
                      onClick={() => setEditingExtraction(false)}
                      className="btn-secondary btn-sm"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => {
                        setCustomExtraction(customExtractionDraft);
                        setEditingExtraction(false);
                        apiMutate('PUT', '/api/knowledge/policies/extraction/custom', { custom: customExtractionDraft }).catch(() => {});
                      }}
                      className="px-4 py-1.5 bg-[#B88719] text-white rounded-xl text-xs font-black uppercase tracking-wider hover:bg-[#8A5A00] transition-all flex items-center gap-1.5 cursor-pointer"
                    >
                      <Save className="w-3.5 h-3.5" />
                      Save
                    </button>
                  </div>
                </div>
              ) : customExtraction ? (
                <pre className="text-[11px] font-mono text-[#2A2A2A] leading-relaxed whitespace-pre-wrap bg-[#FFFBF0] border border-[#BFA66A] rounded-xl p-4 flex-1">
                  {customExtraction}
                </pre>
              ) : (
                <div className="flex flex-col items-center justify-center flex-1 py-10 gap-3 text-center">
                  <div className="w-10 h-10 rounded-2xl bg-[#FFF9E8] border border-[#BFA66A] flex items-center justify-center">
                    <Sparkles className="w-5 h-5 text-[#BFA66A]" />
                  </div>
                  <div>
                    <p className="text-sm font-bold text-[#3F3F3F]">No custom directives yet</p>
                    <p className="text-[11px] text-[#5F5F5F] mt-0.5">
                      Click Edit to append custom extraction instructions to the base policy.
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>

        </div>
      </section>

    </div>
  );
};
