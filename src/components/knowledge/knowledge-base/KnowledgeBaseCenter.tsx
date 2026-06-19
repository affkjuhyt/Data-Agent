/**
 * KnowledgeBaseCenter — manage the tenant's knowledge bases (the "kind" of knowledge
 * a document belongs to). Documents pick one at ingestion; retrieval is scoped to it.
 *
 * Talks to the real backend CRUD at /api/knowledge/knowledge-bases.
 */
import { useEffect, useState } from 'react';
import { Boxes, Plus, Trash2, Loader2, AlertCircle, X } from 'lucide-react';
import { apiGet, apiMutate } from '../../../lib/apiClient';

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  active: boolean;
  added_by: string;
  added_when: string;
}

export function KnowledgeBaseCenter() {
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<KnowledgeBase[]>('/api/knowledge/knowledge-bases');
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load knowledge bases');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const handleCreate = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await apiMutate('POST', '/api/knowledge/knowledge-bases', {
        name: name.trim(),
        description: description.trim(),
      });
      setName('');
      setDescription('');
      setShowCreate(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create knowledge base');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (pendingDeleteId !== id) { setPendingDeleteId(id); return; }
    try {
      await apiMutate('DELETE', `/api/knowledge/knowledge-bases/${id}`);
      setPendingDeleteId(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete knowledge base');
    }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <Boxes className="w-5 h-5 text-[#B88719]" />
          <h2 className="text-lg font-semibold text-[#1E1B15]">Knowledge Groups</h2>
        </div>
        <button
          onClick={() => setShowCreate((s) => !s)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#B88719] text-white text-sm font-medium hover:bg-[#9c7115] transition-colors"
        >
          <Plus className="w-4 h-4" /> New Knowledge Group
        </button>
      </div>
      <p className="text-sm text-[#6b6457] mb-5">
        The kind of knowledge a document belongs to. Documents are tagged with one at
        ingestion so retrieval can be scoped to a single knowledge group.
      </p>

      {error && (
        <div className="flex items-center gap-2 mb-4 px-3 py-2 rounded-md bg-red-50 text-red-700 text-sm">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {showCreate && (
        <div className="mb-5 p-4 rounded-lg border border-[#E7DFcf] bg-[#FAF6ED]">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-[#1E1B15]">Create Knowledge Group</h3>
            <button onClick={() => setShowCreate(false)} className="text-[#6b6457] hover:text-[#1E1B15]">
              <X className="w-4 h-4" />
            </button>
          </div>
          <label className="block text-xs font-medium text-[#6b6457] mb-1">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Finance, Legal, Engineering"
            className="w-full mb-3 px-3 py-2 rounded-md border border-[#E7DFcf] bg-white text-sm focus:outline-none focus:ring-2 focus:ring-[#B88719]/40"
          />
          <label className="block text-xs font-medium text-[#6b6457] mb-1">Description (optional)</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            placeholder="What kind of knowledge does this base hold?"
            className="w-full mb-3 px-3 py-2 rounded-md border border-[#E7DFcf] bg-white text-sm focus:outline-none focus:ring-2 focus:ring-[#B88719]/40"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setShowCreate(false)}
              className="px-3 py-1.5 rounded-md text-sm text-[#6b6457] hover:bg-[#EFE8d8]"
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={!name.trim() || saving}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#B88719] text-white text-sm font-medium disabled:opacity-50 hover:bg-[#9c7115]"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />} Create
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-[#6b6457] text-sm py-8 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading…
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-[#6b6457]">
          <Boxes className="w-8 h-8 mx-auto mb-2 opacity-40" />
          <p className="text-sm">No knowledge bases yet. Create one to start tagging documents.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((kb) => (
            <div
              key={kb.id}
              className="flex items-center justify-between px-4 py-3 rounded-lg border border-[#E7DFcf] bg-white"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-[#1E1B15]">{kb.name}</span>
                  {!kb.active && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#EFE8d8] text-[#6b6457]">inactive</span>
                  )}
                </div>
                {kb.description && <p className="text-xs text-[#6b6457] truncate">{kb.description}</p>}
                <p className="text-[11px] text-[#9c9484] mt-0.5">
                  {kb.added_by ? `${kb.added_by} · ` : ''}{kb.added_when}
                </p>
              </div>
              <button
                onClick={() => handleDelete(kb.id)}
                className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md text-sm transition-colors ${
                  pendingDeleteId === kb.id
                    ? 'bg-red-600 text-white'
                    : 'text-red-600 hover:bg-red-50'
                }`}
              >
                <Trash2 className="w-4 h-4" />
                {pendingDeleteId === kb.id ? 'Confirm' : ''}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
