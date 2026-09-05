import React, { useState, useRef, useEffect } from 'react';
import {
  FileText,
  ArrowRight,
  Cpu,
  Zap,
  ChevronLeft,
  Lock,
  Upload,
  Check,
  AlertCircle,
  X as XIcon,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../../lib/utils';
import { useAppState } from '../../../AppStateContext';
import { STEPS, SOURCES, formatBytes } from './ingest.data';
import { apiUpload, apiGet, apiMutate } from '../../../lib/apiClient';

interface KnowledgeBaseOption { id: string; name: string; }

const SPREADSHEET_EXTENSIONS = ['xlsx', 'xls', 'csv'];
const isSpreadsheetFile = (filename: string): boolean => {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  return SPREADSHEET_EXTENSIONS.includes(ext);
};

export const IngestionWizard = ({ onComplete, onCancel }: { onComplete: () => void, onCancel: () => void }) => {
  const { addDocument, user } = useAppState();
  const [currentStep, setCurrentStep] = useState(0);
  const [selectedSource, setSelectedSource] = useState<'video' | 'image' | 'doc' | 'web' | null>(null);

  // Form Fields
  const [assetName, setAssetName] = useState('');
  const [webUrl, setWebUrl] = useState('');
  const [author, setAuthor] = useState('');
  const [publishedDate, setPublishedDate] = useState('');

  // Knowledge base (kind of knowledge) the document belongs to — loaded from backend.
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseOption[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState('');
  useEffect(() => {
    apiGet<KnowledgeBaseOption[]>('/api/knowledge/knowledge-bases')
      .then((data) => setKnowledgeBases(Array.isArray(data) ? data : []))
      .catch(() => setKnowledgeBases([]));
  }, []);

  // Local uploaded state — an array so multiple files can be queued in one
  // pass; single-file mode (the common case) is just this array with one
  // item and behaves exactly as before (custom name, per-file metadata).
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const isMultiFile = selectedFiles.length > 1;
  const firstFile = selectedFiles[0] ?? null;

  // Opt-in: LLM proposes a table schema from the spreadsheet, a human
  // reviews it (AssetInventory's "Review Schema" button, once the file
  // lands in Bronze), and only on approval is a real Postgres table
  // created. Left off (the default), a spreadsheet goes through the exact
  // same RAG/Qdrant path as any other document — nothing about that path
  // changes here. Only offered when uploading a single spreadsheet file —
  // structured-table review is a per-document flow.
  const [structuredMode, setStructuredMode] = useState(false);

  // Ingest state
  const [isAddingInProcess, setIsAddingInProcess] = useState(false);
  const [ingestionLogs, setIngestionLogs] = useState<string[]>([]);

  // Locked parameters
  const [imageMetadata, setImageMetadata] = useState({
    type: 'PNG',
    file_size: '3.4 MB',
    height: '1080px',
    width: '1920px',
    color_space: 'sRGB (Display P3)'
  });

  const [videoMetadata, setVideoMetadata] = useState({
    type: 'MP4 / H.264',
    file_size: '52.4 MB',
    height: '2160p (4K)',
    width: '3840px',
    total_frame: '24,800'
  });

  const handleSourceSelect = (sourceId: 'video' | 'image' | 'doc' | 'web') => {
    setSelectedSource(sourceId);
    setCurrentStep(1); // Auto-advance instantly on choice! Removes the "Configure" button step.
    setErrorMsg(null);
  };

  const handleFileUploadClick = () => {
    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newFiles = Array.from(e.target.files ?? []);
    if (newFiles.length === 0) return;
    // Re-selecting the same <input> fires onChange again with the same
    // FileList reference cleared — reset it so picking the same file twice
    // in a row (e.g. after removing it) still triggers a change event.
    e.target.value = '';

    setSelectedFiles(prev => [...prev, ...newFiles]);
    const allFiles = [...selectedFiles, ...newFiles];
    const first = newFiles[0];
    const sizeFormatted = formatBytes(first.size);

    // Custom naming only makes sense for a single file — multiple files
    // each keep their own filename as the document name (set at submit time).
    if (allFiles.length === 1) {
      setAssetName(first.name.replace(/\.[^/.]+$/, ''));
    }
    if (!isSpreadsheetFile(first.name) || allFiles.length > 1) setStructuredMode(false);

    if (selectedSource === 'image') {
      setImageMetadata({
        type: first.name.split('.').pop()?.toUpperCase() || 'PNG',
        file_size: sizeFormatted,
        height: '1440px',
        width: '2560px',
        color_space: 'sRGB (Display P3)'
      });
    } else if (selectedSource === 'video') {
      setVideoMetadata({
        type: first.name.split('.').pop()?.toUpperCase() || 'MP4',
        file_size: sizeFormatted,
        height: '1080px',
        width: '1920px',
        total_frame: '11,250'
      });
    }
    setErrorMsg(null);
  };

  const removeFile = (idx: number) => {
    setSelectedFiles(prev => prev.filter((_, i) => i !== idx));
  };

  const executeAddData = async () => {
    if (!selectedSource) return;

    if (selectedSource === 'web') {
      if (!assetName.trim()) {
        setErrorMsg('Web Portal Name is required.');
        return;
      }
      if (!webUrl.trim()) {
        setErrorMsg('URL Endpoint is required.');
        return;
      }
      if (!webUrl.startsWith('http://') && !webUrl.startsWith('https://')) {
        setErrorMsg('Web URLs must declare absolute http:// or https:// protocol.');
        return;
      }
    } else {
      if (selectedFiles.length === 0) {
        setErrorMsg('Please select at least one file to upload.');
        return;
      }
      if (!isMultiFile && !assetName.trim()) {
        setErrorMsg('A valid file name is required.');
        return;
      }
    }

    setIsAddingInProcess(true);
    setErrorMsg(null);
    setIngestionLogs(['Establishing Bronze-Level ingestion pipeline stream...']);

    const uploadOneFile = async (file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      // NOTE: Do NOT include role_id, tenant_id, or user_id in body — they
      // are auto-injected as headers (X-Role, X-Tenant-Id, X-User-Id). A
      // single file keeps the custom Knowledge Name; multiple files each
      // take their own filename (no single name would fit all of them, and
      // the backend requires unique names per tenant anyway).
      const docName = isMultiFile ? file.name.replace(/\.[^/.]+$/, '') : assetName;
      formData.append('name', docName);
      formData.append('abstract', docName);
      formData.append('language', 'english');
      formData.append('source_type', selectedSource as string);
      if (knowledgeBaseId) formData.append('knowledge_base_id', knowledgeBaseId);
      if (structuredMode && !isMultiFile) formData.append('ingestion_mode', 'structured_table');
      // doc_metadata field names MUST mirror the backend metadata models
      // (Document.doc_type, Image.image_type, Video.video_type) — those *_type
      // fields are required, so sending a generic "type" breaks the bronze insert.
      const fileExt = (file.name.split('.').pop() || 'FILE').toUpperCase();
      const apiMetadata =
        selectedSource === 'image'
          ? {
              image_type:  imageMetadata.type,
              height:      parseInt(imageMetadata.height, 10) || 0,
              width:       parseInt(imageMetadata.width, 10) || 0,
              color_space: imageMetadata.color_space,
            }
          : selectedSource === 'video'
          ? {
              video_type:  videoMetadata.type,
              height:      parseInt(videoMetadata.height, 10) || 0,
              width:       parseInt(videoMetadata.width, 10) || 0,
              total_frame: parseInt(String(videoMetadata.total_frame).replace(/[^0-9]/g, ''), 10) || 0,
            }
          : {
              doc_type:       `Doc/${fileExt}`,
              author:         author || 'Anonymous',
              published_date: publishedDate || new Date().toISOString().split('T')[0],
            };
      formData.append('doc_metadata', JSON.stringify(apiMetadata));

      const result = await apiUpload<{ data_id: string; name: string; path: string; layer: string }>(
        '/api/pipeline/ingestion/bronze',
        formData
      );
      setIngestionLogs(prev => [...prev, `Registered: ${result.name} (${result.data_id})`]);
      addDocument({
        data_id: result.data_id,
        name: result.name,
        source_type: selectedSource ?? 'doc',
        current_tier: 'bronze',
        status: 'RAW',
        added_on: new Date().toISOString(),
        added_by: user.name || 'AI Engineer',
        ingestion_mode: (structuredMode && !isMultiFile) ? 'structured_table' : 'document',
        metadata: {
          doc_type: selectedSource === 'doc' ? `Doc/${fileExt}` : selectedSource === 'image' ? 'Image' : 'Video',
          warehouse_type: null,
          author: selectedSource === 'doc' ? (author || null) : null,
          published_date: publishedDate || new Date().toISOString().split('T')[0],
        },
      });
    };

    try {
      // Web portal: no file — register the URL as bronze. The bronze→silver step
      // fetches the page, strips boilerplate to markdown, and Docling-chunks it.
      if (selectedSource === 'web') {
        const result = await apiMutate<{ data_id: string; name: string; url: string; layer: string }>(
          'POST',
          '/api/pipeline/ingestion/bronze/web',
          {
            name: assetName,
            url: webUrl,
            web_name: assetName,
            ...(knowledgeBaseId ? { knowledge_base_id: knowledgeBaseId } : {}),
          },
        );
        setIngestionLogs(prev => [...prev, `Web portal registered in database: ${result.data_id}`]);
        addDocument({
          data_id: result.data_id,
          name: result.name,
          source_type: 'web',
          current_tier: 'bronze',
          status: 'RAW',
          added_on: new Date().toISOString(),
          added_by: user.name || 'AI Engineer',
          metadata: {
            doc_type: 'web',
            warehouse_type: null,
            url: webUrl,
          },
        });
      } else {
        // Sequential, not parallel: keeps the log readable and avoids
        // hammering the gateway with N simultaneous uploads for a batch.
        for (const file of selectedFiles) {
          await uploadOneFile(file);
        }
      }

      setTimeout(() => {
        onComplete();
      }, 800);
    } catch (error) {
      setErrorMsg(`Upload failed: ${(error as Error).message}`);
      setIsAddingInProcess(false);
    }
  };

  const prevStep = () => {
    setErrorMsg(null);
    setCurrentStep(0);
  };

  return (
    <div className="flex flex-col h-full bg-[#FCFBF7] text-[#111111] select-none">
      
      {/* Hidden file selector trigger */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileChange}
        className="hidden"
        multiple
        accept={
          selectedSource === 'image' ? 'image/*' :
          selectedSource === 'video' ? 'video/*' :
          selectedSource === 'doc' ? '.pdf,.docx,.txt,.md,.xlsx,.xls,.csv' :
          '*'
        }
      />

      {/* Header and Step Indicators */}
      <div className="px-8 py-4 bg-[#FFFDF6] border-b border-[#BFA66A]">
        {/* Progress Bar */}
        <div className="mb-4">
          <div className="w-full h-2 bg-[#E8DCC8] rounded-full overflow-hidden">
            <motion.div
              initial={{ width: "0%" }}
              animate={{ width: `${((currentStep + 1) / STEPS.length) * 100}%` }}
              transition={{ duration: 0.4, ease: "easeOut" }}
              className="h-full bg-gradient-to-r from-[#B88719] to-[#8A5A00] rounded-full shadow-sm"
            />
          </div>
          <div className="flex justify-between mt-1.5 px-1">
            <span className="text-[9px] font-black text-[#5A5A5A] uppercase tracking-wider">Progress</span>
            <span className="text-[9px] font-bold text-[#8A5A00]">{currentStep + 1} of {STEPS.length}</span>
          </div>
        </div>

        {/* Step Indicators */}
        <div className="flex justify-between items-center relative max-w-sm mx-auto">
          <div className="absolute left-0 right-0 h-0.5 bg-[#D6C79F] top-1/2 -translate-y-1/2 z-0" />
          {STEPS.map((step, idx) => (
            <div key={step.id} className="relative z-10 flex flex-col items-center gap-1">
              <motion.div
                animate={{
                  scale: currentStep === idx ? 1.15 : 1,
                }}
                className={cn(
                  "w-7 h-7 rounded-full border-2 flex items-center justify-center text-[11px] font-black transition-all duration-300",
                  currentStep === idx
                    ? "bg-[#B88719] border-[#8A5A00] text-white shadow-md shadow-[#B88719]/30"
                    : currentStep > idx
                    ? "bg-[#2F4F0B] border-[#6B8E23] text-white"
                    : "bg-white border-[#BFA66A] text-[#5A5A5A]"
                )}>
                {currentStep > idx ? <Check className="w-3.5 h-3.5 stroke-[3]" /> : idx + 1}
              </motion.div>
              <span className={cn(
                "text-[9px] font-bold uppercase tracking-wider transition-colors",
                currentStep === idx ? "text-[#8A5A00]" : currentStep > idx ? "text-[#2F4F0B]" : "text-[#5A5A5A]"
              )}>{step.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Main Body Canvas - Beautifully scrollable vertically if too long */}
      <div className="flex-1 overflow-y-auto p-6 md:p-8 custom-scrollbar">
        <AnimatePresence mode="wait">
          
          {/* A. Progress State */}
          {isAddingInProcess ? (
            <motion.div
              key="process-state"
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              className="flex flex-col items-center justify-center h-full py-12 text-center"
            >
              <div className="relative mb-6">
                <div className="w-14 h-14 rounded-full border-4 border-[#BFA66A] border-t-[#B88719] animate-spin" />
                <Cpu className="w-5 h-5 text-[#B88719] absolute top-4.5 left-4.5 animate-pulse" />
              </div>

              <h3 className="text-lg font-bold font-display text-[#111111] uppercase tracking-wide">Adding Data Stream</h3>
              <p className="text-[#5A5A5A] text-xs mt-1">Routing file credentials to active Bronze storage segment</p>

              <div className="w-full max-w-md bg-[#111111] border-2 border-[#8A5A00] rounded-2xl p-5 text-left font-mono mt-6 h-40 overflow-y-auto shadow-md">
                {ingestionLogs.map((log, idx) => (
                  <div key={idx} className="flex gap-2 items-start text-xs py-0.5">
                    <span className="text-[#B88719] shrink-0 font-bold">&gt;</span>
                    <span className={idx === ingestionLogs.length - 1 ? "text-[#FFFDF6] font-bold" : "text-[#5F5F5F]"}>
                      {log}
                    </span>
                  </div>
                ))}
              </div>
            </motion.div>
          )

          /* B. Step 1: Select Source Type (Clicking automatically triggers progression!) */
          : currentStep === 0 ? (
            <motion.div
              key="step-select"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="space-y-6"
            >
              <div className="text-center max-w-md mx-auto">
                <h3 className="text-lg font-bold text-[#111111] tracking-tight uppercase font-display">Select Data Source</h3>
                <p className="text-[#5A5A5A] text-xs mt-1">Click one option below to instantly begin metadata configuration</p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-xl mx-auto">
                {SOURCES.map(src => {
                  const SvgIcon = src.icon;
                  return (
                    <button
                      key={src.id}
                      onClick={() => handleSourceSelect(src.id as any)}
                      className="p-5 rounded-2xl text-left border-2 transition-all outline-none duration-150 relative overflow-hidden flex flex-col justify-between h-40 border-[#BFA66A] hover:border-[#8A5A00] bg-white hover:bg-[#FFFDF3] hover:shadow-md cursor-pointer"
                    >
                      <div className="w-9 h-9 rounded-xl flex items-center justify-center mb-4 bg-[#FFFDF6] border border-[#BFA66A] text-[#8A5A00]">
                        <SvgIcon className="w-4.5 h-4.5" />
                      </div>
                      <div>
                        <h4 className="text-xs font-black uppercase tracking-wider text-[#111111]">{src.name}</h4>
                        <p className="text-[10px] text-[#5A5A5A] mt-1 leading-normal">{src.desc}</p>
                      </div>
                    </button>
                  );
                })}
              </div>
            </motion.div>
          )

          /* C. Step 2: Configure Properties (Vertical form layouts with labels stacked atop inputs) */
          : (
            <motion.div
              key="step-metadata"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="space-y-5 max-w-3xl mx-auto"
            >
              <div className="text-center mb-2">
                <span className="inline-flex items-center gap-1.5 px-3 py-1 bg-[#FFF9E8] border border-[#BFA66A] rounded-full text-[10px] text-[#8A5A00] font-mono font-bold uppercase mb-2">
                  <Zap className="w-3 h-3 text-[#B88719]" />
                  Active Ingestion: {selectedSource && SOURCES.find(s => s.id === selectedSource)?.name}
                </span>
                <h3 className="text-lg font-bold text-[#111111] uppercase tracking-tight">Configure properties</h3>
                <p className="text-[#5A5A5A] text-xs">Verify values. System registers parameters and bundles payload directly to Bronze tier.</p>
              </div>

              {/* Stacked Vertical Row Form */}
              <div className="bg-white border-2 border-[#BFA66A] rounded-2xl p-6 space-y-5 shadow-sm">
                
                {/* 1. Upload Section in full vertical stack */}
                {selectedSource !== 'web' && (
                  <div className="flex flex-col gap-1.5 pb-4 border-b border-[#BFA66A]">
                    <span className="text-[10px] font-black uppercase tracking-widest text-[#111111]">Source Payload *</span>
                    <span className="text-[10px] text-[#5A5A5A] mb-1">Upload one or more raw resource files from your machine</span>

                    <div className="w-full flex flex-col gap-2">
                      {selectedFiles.map((f, idx) => (
                        <div key={`${f.name}-${idx}`} className="p-3 bg-[#FFFDF6] border border-[#BFA66A] rounded-xl flex items-center justify-between">
                          <div className="flex items-center gap-3 overflow-hidden">
                            <div className="p-2 bg-[#F3E2A7] border border-[#BFA66A] text-[#8A5A00] rounded-lg shrink-0">
                              <FileText className="w-4 h-4" />
                            </div>
                            <div className="overflow-hidden">
                              <div className="text-xs font-bold text-[#111111] truncate">{f.name}</div>
                              <div className="text-[10px] font-mono text-[#5A5A5A] mt-0.5">
                                {formatBytes(f.size)} • {(f.name.split('.').pop() || 'RAW').toUpperCase()}
                              </div>
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => removeFile(idx)}
                            className="p-1.5 text-[#5A5A5A] hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors shrink-0"
                            aria-label={`Remove ${f.name}`}
                          >
                            <XIcon className="w-4 h-4" />
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        onClick={handleFileUploadClick}
                        className={cn(
                          'w-full bg-[#FFFDF6] hover:bg-[#FFF9E8] border-2 border-dashed border-[#BFA66A] hover:border-[#8A5A00] rounded-xl flex flex-col items-center justify-center gap-1.5 transition-all group cursor-pointer',
                          selectedFiles.length === 0 ? 'py-5' : 'py-3'
                        )}
                      >
                        <Upload className="w-4.5 h-4.5 text-[#8A5A00] group-hover:scale-105 transition-transform" />
                        <span className="text-[10px] font-black uppercase tracking-wider text-[#111111]">
                          {selectedFiles.length === 0 ? 'Upload Resource' : 'Add More Files'}
                        </span>
                      </button>
                    </div>
                    {isMultiFile && (
                      <span className="text-[10px] text-[#5A5A5A] pt-1">
                        {selectedFiles.length} files selected — each keeps its own filename as its Knowledge Name.
                      </span>
                    )}

                    {/* Spreadsheet-only: opt into LLM-proposed schema + human
                        review before a real Postgres table gets created.
                        Off by default — the file otherwise goes through the
                        exact same RAG/Qdrant path as any other document.
                        Single-file only — structured-table review is a
                        per-document flow. */}
                    {selectedSource === 'doc' && !isMultiFile && firstFile && isSpreadsheetFile(firstFile.name) && (
                      <div className="flex flex-col gap-1.5 pt-4">
                        <span className="text-[10px] font-black uppercase tracking-widest text-[#111111]">How should this data be added?</span>
                        <div className="content-card content-card--static inline-flex p-1 shadow-2xs w-fit">
                          <button
                            type="button"
                            onClick={() => setStructuredMode(false)}
                            className={cn(
                              'px-4 py-2 rounded-xl text-[10px] font-bold uppercase tracking-wide transition-all',
                              !structuredMode ? 'bg-[#B88719] text-white shadow-sm' : 'text-[#5A5A5A] hover:bg-[#FAF6ED]'
                            )}
                          >
                            Add to knowledge base
                          </button>
                          <button
                            type="button"
                            onClick={() => setStructuredMode(true)}
                            className={cn(
                              'px-4 py-2 rounded-xl text-[10px] font-bold uppercase tracking-wide transition-all',
                              structuredMode ? 'bg-[#B88719] text-white shadow-sm' : 'text-[#5A5A5A] hover:bg-[#FAF6ED]'
                            )}
                          >
                            Convert to structured table
                          </button>
                        </div>
                        <span className="text-[10px] text-[#5A5A5A]">
                          {structuredMode
                            ? 'An LLM will propose a table schema from this file for you to review — no table is created until you approve it.'
                            : 'Default: chunked and embedded for semantic search, same as any other document.'}
                        </span>
                      </div>
                    )}
                  </div>
                )}

                {/* 2. Common Name row (Vertical Stack) */}
                <div className="flex flex-col gap-1.5 pb-2">
                  <label className="text-[10px] font-black uppercase tracking-widest text-[#111111]">
                    {selectedSource === 'web' ? 'Web Portal Name *' : 'Knowledge Name *'}
                  </label>
                  <span className="text-[10px] text-[#5A5A5A] mb-1">
                    {isMultiFile ? 'Each file keeps its own filename as its name' : 'Dynamic system identifier assigned to resource'}
                  </span>
                  {isMultiFile ? (
                    <div className="field-input text-xs font-mono text-[#5A5A5A] bg-[#FAF6ED] cursor-not-allowed">
                      {selectedFiles.length} files — named individually
                    </div>
                  ) : (
                    <input
                      type="text"
                      placeholder={selectedSource === 'web' ? "e.g., AeroFlow Web Documentation" : "e.g., enterprise_sso_schema"}
                      value={assetName}
                      onChange={(e) => setAssetName(e.target.value)}
                      className="field-input text-xs font-mono"
                    />
                  )}
                </div>

                {/* 2b. Knowledge Base selector — the kind of knowledge this doc belongs to */}
                <div className="flex flex-col gap-1.5 pb-2">
                  <label className="text-[10px] font-black uppercase tracking-widest text-[#111111]">Knowledge Base</label>
                  <span className="text-[10px] text-[#5A5A5A] mb-1">Scopes retrieval to a kind of knowledge (optional)</span>
                  <select
                    value={knowledgeBaseId}
                    onChange={(e) => setKnowledgeBaseId(e.target.value)}
                    className="field-input text-xs"
                  >
                    <option value="">— None (unscoped) —</option>
                    {knowledgeBases.map((kb) => (
                      <option key={kb.id} value={kb.id}>{kb.name}</option>
                    ))}
                  </select>
                </div>

                {/* 3. Web URL parameters row (Vertical Stack) */}
                {selectedSource === 'web' && (
                  <div className="flex flex-col gap-1.5 pb-2">
                    <label className="text-[10px] font-black uppercase tracking-widest text-[#111111]">URL Endpoint *</label>
                    <span className="text-[10px] text-[#5A5A5A] mb-1">Absolute online source crawl destination</span>
                    <input
                      type="text"
                      placeholder="https://docs.aeroflow.ai/index"
                      value={webUrl}
                      onChange={(e) => setWebUrl(e.target.value)}
                      className="field-input text-xs font-mono"
                    />
                  </div>
                )}

                {/* 4. Document-specific parameter rows (Vertical Stack) */}
                {selectedSource === 'doc' && (
                  <>
                    <div className="flex flex-col gap-1.5 pb-2">
                      <label className="text-[10px] font-black uppercase tracking-widest text-[#111111]">Author / Contributor</label>
                      <span className="text-[10px] text-[#5A5A5A] mb-1">Author name tagged inside index ledger</span>
                      <input
                        type="text"
                        placeholder="Optional"
                        value={author}
                        onChange={(e) => setAuthor(e.target.value)}
                        className="field-input text-xs"
                      />
                    </div>

                    <div className="flex flex-col gap-1.5 pb-2">
                      <label className="text-[10px] font-black uppercase tracking-widest text-[#111111]">Published Date</label>
                      <span className="text-[10px] text-[#5A5A5A] mb-1">Time or year signature of artifact publishment</span>
                      <input
                        type="date"
                        value={publishedDate}
                        onChange={(e) => setPublishedDate(e.target.value)}
                        className="field-input text-xs font-mono"
                      />
                    </div>
                  </>
                )}

                {/* 5. Image details metadata row (Vertical Stack) */}
                {selectedSource === 'image' && (
                  <div className="flex flex-col gap-2 pt-2 border-t border-[#BFA66A]">
                    <div className="text-[10px] font-black text-[#8A5A00] flex items-center gap-1.5">
                      <Lock className="w-3.5 h-3.5 text-[#B88719]" />
                      AUTO-EXTRACTED METADATA (SYSTEM LOCKED)
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 bg-[#FFFDF6] border border-[#BFA66A] p-3 rounded-xl">
                      {[
                        { label: 'format', val: imageMetadata.type },
                        { label: 'file_size', val: imageMetadata.file_size },
                        { label: 'height', val: imageMetadata.height },
                        { label: 'width', val: imageMetadata.width },
                        { label: 'color_space', val: imageMetadata.color_space }
                      ].map(item => (
                        <div key={item.label} className="p-2 border border-[#BFA66A] rounded-lg bg-white">
                          <span className="text-[9px] font-bold text-[#5A5A5A] uppercase tracking-wider block mb-0.5">{item.label}</span>
                          <span className="font-mono text-[11px] text-[#111111] font-semibold">{item.val}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 6. Video details metadata row (Vertical Stack) */}
                {selectedSource === 'video' && (
                  <div className="flex flex-col gap-2 pt-2 border-t border-[#BFA66A]">
                    <div className="text-[10px] font-black text-[#8A5A00] flex items-center gap-1.5">
                      <Lock className="w-3.5 h-3.5 text-[#B88719]" />
                      AUTO-EXTRACTED PARAMETERS (SYSTEM LOCKED)
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 bg-[#FFFDF6] border border-[#BFA66A] p-3 rounded-xl">
                      {[
                        { label: 'type', val: videoMetadata.type },
                        { label: 'file_size', val: videoMetadata.file_size },
                        { label: 'height', val: videoMetadata.height },
                        { label: 'width', val: videoMetadata.width },
                        { label: 'total_frame', val: videoMetadata.total_frame }
                      ].map(item => (
                        <div key={item.label} className="p-2 border border-[#BFA66A] rounded-lg bg-white">
                          <span className="text-[9px] font-bold text-[#5A5A5A] uppercase tracking-wider block mb-0.5">{item.label}</span>
                          <span className="font-mono text-[11px] text-[#111111] font-semibold">{item.val}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Error message display slot */}
      {errorMsg && !isAddingInProcess && (
        <div className="px-8 pb-3">
          <div className="px-4 py-2.5 bg-[#FAF1F1] border border-[#C94A4A] rounded-xl flex items-center gap-3 text-xs text-[#9F1D1D] font-medium leading-relaxed">
            <AlertCircle className="w-4 h-4 shrink-0 text-[#9F1D1D]" />
            <span>{errorMsg}</span>
          </div>
        </div>
      )}

      {/* Footer Navigation tray - Custom adjusted */}
      {!isAddingInProcess && (
        <div className="p-6 border-t border-[#BFA66A] bg-[#FAF6EA] flex justify-between items-center shrink-0">
          {currentStep === 1 ? (
            <button
              onClick={prevStep}
              className="btn-secondary h-10 px-4 flex items-center gap-1.5"
            >
              <ChevronLeft className="w-4 h-4" />
              BACK
            </button>
          ) : (
            <div className="w-4" />
          )}
          
          <div className="flex gap-2">
            <button
              onClick={onCancel}
              className="btn-secondary h-10 px-5 text-xs text-[#5A5A5A] border-none hover:bg-[#F3E2A7]/20"
            >
              Cancel
            </button>

            {currentStep === 1 && (
              <button
                onClick={executeAddData}
                className="btn-gold h-10 px-6 font-black uppercase text-glow"
              >
                Add Data File
                <ArrowRight className="w-4.5 h-4.5 ml-1 inline-block" />
              </button>
            )}
          </div>
        </div>
      )}

    </div>
  );
};
