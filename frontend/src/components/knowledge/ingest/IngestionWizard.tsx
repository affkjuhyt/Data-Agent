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
  Pencil,
  X as XIcon,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../../lib/utils';
import { useAppState } from '../../../AppStateContext';
import { STEPS, SOURCES, DATA_PURPOSES, formatBytes } from './ingest.data';
import { apiUpload, apiGet, apiMutate } from '../../../lib/apiClient';
import {
  ConnectorType, DataPurpose, DATA_PURPOSE_LABEL, DOCUMENT_FEED_CONNECTOR_TYPES, CONNECTOR_PILL_LABEL, CONNECTOR_PILL_DESCRIPTION,
  ScheduleState, DEFAULT_SCHEDULE_STATE, buildCron, parseQueryParams, cronToHuman, cronToScheduleState,
  isCronRepresentable,
} from '../sources/sourceShared';
import { SchedulePicker } from '../sources/SchedulePicker';
import { RestSdmxFields, RecursiveCrawlFields, PromotionModeControl, RestSdmxFieldsValue, RecursiveCrawlFieldsValue } from '../sources/SourceConfigFields';

interface KnowledgeBaseOption { id: string; name: string; }

const SPREADSHEET_EXTENSIONS = ['xlsx', 'xls', 'csv'];
const isSpreadsheetFile = (filename: string): boolean => {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  return SPREADSHEET_EXTENSIONS.includes(ext);
};

// "Document Hub" method — file bytes now, vs. a KBSource this screen also
// creates (a scheduled/one-off URL or a feed/API endpoint). "Web Portal" has
// no upload method — crawling a URL is the whole point of that tile.
type DocMethod = 'upload' | 'url' | 'feed';

// Every connector a Bulk Import row can end up as — 'upload' is deliberately
// excluded (bookkeeping-only, never something a create request selects).
const BULK_CONNECTOR_TYPES: ConnectorType[] = ['http_file', 'web_crawl', 'web_crawl_headless', 'rss', 'rest_api', 'sdmx'];

interface BulkPreviewRow {
  title: string; publisher: string | null; connector_type: string; url: string;
  cron: string; promotion_mode: string | null; purpose: string;
  [key: string]: unknown;
}

export const IngestionWizard = ({ onComplete, onCancel }: { onComplete: () => void, onCancel: () => void }) => {
  const { addDocument, user } = useAppState();
  const [currentStep, setCurrentStep] = useState(0);
  const [selectedSource, setSelectedSource] = useState<'video' | 'image' | 'doc' | 'web' | null>(null);

  // Form Fields
  const [assetName, setAssetName] = useState('');
  const [author, setAuthor] = useState('');
  const [publishedDate, setPublishedDate] = useState('');

  // Data Purpose (Phase D) — every ingestion path stamps this on the KBData
  // row(s) it creates. Non-production data is fully hidden from RAG/search
  // by default (see qdrant_connector.py) — visible only via Data Layers.
  const [purpose, setPurpose] = useState<DataPurpose>('production');

  // Document Hub's 3-way method — Upload keeps today's one-shot file flow;
  // From URL / From Feed-or-API instead register a KBSource (Source
  // Registry), same as a source created there directly.
  const [docMethod, setDocMethod] = useState<DocMethod>('upload');
  const [feedConnector, setFeedConnector] = useState<ConnectorType>('rest_api');
  // Web Portal: a static fetch vs. Playwright-rendered — replaces the old
  // two-tile Web Page / Web Page (JS) split with a single toggle.
  const [renderJs, setRenderJs] = useState(false);

  // Shared by every KBSource-creating branch (doc "From URL"/"From
  // Feed-or-API", and Web Portal).
  const [sourceUrl, setSourceUrl] = useState('');
  const [restSdmx, setRestSdmx] = useState<RestSdmxFieldsValue>({
    queryParamsText: '', openbaoPath: '', authStyle: 'bearer', authParamName: '',
  });
  const [recursiveCfg, setRecursiveCfg] = useState<RecursiveCrawlFieldsValue>({
    recursive: false, maxDepth: 1, sameDomainOnly: true,
  });
  const [schedule, setSchedule] = useState<ScheduleState>({ ...DEFAULT_SCHEDULE_STATE });
  // null = "use the default" (auto once a schedule is picked, manual
  // otherwise — same rule create_source applies server-side).
  const [promotionModeOverride, setPromotionModeOverride] = useState<'manual' | 'auto' | null>(null);
  const effectivePromotionMode: 'manual' | 'auto' =
    promotionModeOverride ?? (schedule.mode !== 'none' ? 'auto' : 'manual');

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

  // Whether this run creates a KBSource (Source Registry) instead of a
  // plain Bronze upload — every method except a Document Hub "Upload" and
  // the video/image tiles.
  const isSourceBased = selectedSource === 'web' || (selectedSource === 'doc' && docMethod !== 'upload');
  const isUpload = !isSourceBased;
  const effectiveConnectorType: ConnectorType | null =
    selectedSource === 'web' ? (renderJs ? 'web_crawl_headless' : 'web_crawl')
    : selectedSource === 'doc' && docMethod === 'url' ? 'http_file'
    : selectedSource === 'doc' && docMethod === 'feed' ? feedConnector
    : null;

  // ── Bulk Import (YAML) — entry point lives on Step 1, ported from
  //    SourceRegistry: paste YAML, parse it into an editable preview table
  //    (title/publisher/URL/schedule per row), let the steward fix rows,
  //    then commit — every created row auto-runs immediately (see
  //    kb_backend's bulk_import_sources_route/_auto_run).
  const [showBulkImport, setShowBulkImport] = useState(false);
  const [bulkText, setBulkText] = useState('');
  const [bulkParsing, setBulkParsing] = useState(false);
  const [bulkPreview, setBulkPreview] = useState<BulkPreviewRow[] | null>(null);
  const [bulkSkipped, setBulkSkipped] = useState<{ title: string; reason: string }[]>([]);
  const [bulkSaving, setBulkSaving] = useState(false);
  const [bulkResult, setBulkResult] = useState<{ createdCount: number; skipped: { title: string; reason: string }[] } | null>(null);
  // Which row's schedule is being edited (a small SchedulePicker expands
  // inline under that row) — nobody reviewing this table should have to read
  // or type raw cron; the picker is the only way to change it.
  const [scheduleEditIdx, setScheduleEditIdx] = useState<number | null>(null);
  const [scheduleDraft, setScheduleDraft] = useState<ScheduleState>({ ...DEFAULT_SCHEDULE_STATE });

  const closeBulkImport = () => {
    setShowBulkImport(false);
    setBulkText('');
    setBulkPreview(null);
    setBulkSkipped([]);
    setBulkResult(null);
    setScheduleEditIdx(null);
  };

  const handleBulkParse = async () => {
    if (!bulkText.trim()) return;
    setBulkParsing(true);
    setBulkResult(null);
    setScheduleEditIdx(null);
    try {
      const res = await apiMutate<{ preview: BulkPreviewRow[]; skipped: { title: string; reason: string }[] }>(
        'POST', '/api/knowledge/sources/bulk-import', { yaml_text: bulkText, dry_run: true },
      );
      // Left exactly as the backend parsed it — empty when the YAML row
      // didn't say (the common case), filled in when it did. A steward must
      // explicitly pick Purpose/Promotion for every row before confirming
      // (see canConfirmBulk) rather than a batch of 20+ rows silently
      // getting a default nobody reviewed.
      setBulkPreview(res.preview.map(row => ({ ...row, purpose: row.purpose || '', promotion_mode: row.promotion_mode || '' })));
      setBulkSkipped(res.skipped);
    } catch (e) {
      setBulkPreview(null);
      setBulkSkipped([{ title: '?', reason: e instanceof Error ? e.message : 'Invalid YAML' }]);
    } finally {
      setBulkParsing(false);
    }
  };

  const updateBulkRow = (idx: number, patch: Partial<BulkPreviewRow>) =>
    setBulkPreview(prev => prev ? prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)) : prev);
  const removeBulkRow = (idx: number) =>
    setBulkPreview(prev => prev ? prev.filter((_, i) => i !== idx) : prev);

  const openScheduleEditor = (idx: number, cron: string) => {
    setScheduleDraft(cronToScheduleState(cron));
    setScheduleEditIdx(idx);
  };
  const applyScheduleEditor = () => {
    if (scheduleEditIdx === null) return;
    const cron = buildCron(
      scheduleDraft.mode, scheduleDraft.time, scheduleDraft.days, scheduleDraft.monthDays,
      scheduleDraft.dayOfMonth, scheduleDraft.months, scheduleDraft.hourEvery, scheduleDraft.minuteEvery,
    );
    updateBulkRow(scheduleEditIdx, { cron });
    setScheduleEditIdx(null);
  };

  // Every row must have an explicit Purpose and Promotion choice — a batch
  // of 20+ rows silently inheriting a default nobody looked at is exactly
  // what this review step exists to prevent.
  const incompleteBulkRows = (bulkPreview || []).filter(r => !r.purpose || !r.promotion_mode).length;

  const confirmBulkImport = async () => {
    if (!bulkPreview || bulkPreview.length === 0 || incompleteBulkRows > 0) return;
    setBulkSaving(true);
    try {
      const res = await apiMutate<{ created: unknown[]; skipped: { title: string; reason: string }[] }>(
        'POST', '/api/knowledge/sources/bulk-import', { rows: bulkPreview, dry_run: false },
      );
      setBulkResult({ createdCount: res.created.length, skipped: res.skipped });
      setBulkPreview(null);
    } catch (e) {
      setBulkResult({ createdCount: 0, skipped: [{ title: '?', reason: e instanceof Error ? e.message : 'Import failed' }] });
    } finally {
      setBulkSaving(false);
    }
  };

  const handleSourceSelect = (sourceId: 'video' | 'image' | 'doc' | 'web') => {
    setSelectedSource(sourceId);
    setDocMethod('upload');
    setFeedConnector('rest_api');
    setRenderJs(false);
    setSourceUrl('');
    setRestSdmx({ queryParamsText: '', openbaoPath: '', authStyle: 'bearer', authParamName: '' });
    setRecursiveCfg({ recursive: false, maxDepth: 1, sameDomainOnly: true });
    setSchedule({ ...DEFAULT_SCHEDULE_STATE });
    setPromotionModeOverride(null);
    setCurrentStep(1); // Auto-advance instantly on choice, straight to Data Purpose.
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

  // Validates whatever Step 2 (Configure Source) collected, before moving on
  // to Step 3 — catches a missing file/URL right where it was meant to be
  // filled in, instead of only at the final Submit.
  const validateConfigureStep = (): string | null => {
    if (isUpload) {
      if (selectedFiles.length === 0) return 'Please select at least one file to upload.';
      return null;
    }
    if (!sourceUrl.trim()) return 'A URL is required.';
    if (!sourceUrl.startsWith('http://') && !sourceUrl.startsWith('https://')) {
      return 'URLs must declare absolute http:// or https:// protocol.';
    }
    return null;
  };

  const goToProperties = () => {
    const err = validateConfigureStep();
    if (err) { setErrorMsg(err); return; }
    setErrorMsg(null);
    setCurrentStep(3);
  };

  const executeAddData = async () => {
    if (!selectedSource) return;

    if (isSourceBased) {
      if (!assetName.trim()) {
        setErrorMsg('A title is required.');
        return;
      }
      const err = validateConfigureStep();
      if (err) { setErrorMsg(err); return; }
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
      formData.append('purpose', purpose);
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
      if (isSourceBased && effectiveConnectorType) {
        // Document "From URL"/"From Feed-or-API" and Web Portal all register
        // a KBSource the same way Source Registry's own Add form does — the
        // worker auto-runs it immediately (see kb_backend's _auto_run), so
        // this ends up in Bronze with no extra click, exactly like a plain
        // upload does today.
        const cron = buildCron(
          schedule.mode, schedule.time, schedule.days, schedule.monthDays, schedule.dayOfMonth,
          schedule.months, schedule.hourEvery, schedule.minuteEvery,
        );
        const isFeed = selectedSource === 'doc' && docMethod === 'feed';
        const result = await apiMutate<{ id: string; title: string }>('POST', '/api/knowledge/sources', {
          title: assetName,
          connector_type: effectiveConnectorType,
          url: sourceUrl.trim(),
          render: renderJs,
          cron,
          openbao_path: isFeed ? (restSdmx.openbaoPath || undefined) : undefined,
          query_params: isFeed ? parseQueryParams(restSdmx.queryParamsText) : undefined,
          auth_style: isFeed && restSdmx.openbaoPath ? restSdmx.authStyle : undefined,
          auth_param_name: isFeed && restSdmx.authStyle === 'query_param' ? (restSdmx.authParamName || undefined) : undefined,
          recursive: selectedSource === 'web' ? recursiveCfg.recursive : undefined,
          max_depth: selectedSource === 'web' ? recursiveCfg.maxDepth : undefined,
          same_domain_only: selectedSource === 'web' ? recursiveCfg.sameDomainOnly : undefined,
          promotion_mode: promotionModeOverride ?? undefined,
          purpose,
        });
        setIngestionLogs(prev => [...prev, `Source registered: ${result.title} (${result.id})`]);
        addDocument({
          data_id: result.id,
          name: result.title,
          source_type: selectedSource === 'web' ? 'web' : 'doc',
          current_tier: 'bronze',
          status: 'RAW',
          added_on: new Date().toISOString(),
          added_by: user.name || 'AI Engineer',
          metadata: {
            doc_type: effectiveConnectorType,
            warehouse_type: null,
            url: sourceUrl.trim(),
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
      setErrorMsg(`${isSourceBased ? 'Source registration' : 'Upload'} failed: ${(error as Error).message}`);
      setIsAddingInProcess(false);
    }
  };

  const prevStep = () => {
    setErrorMsg(null);
    setCurrentStep(s => Math.max(0, s - 1));
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
        <div className="flex justify-between items-center relative max-w-xl mx-auto">
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
                "text-[9px] font-bold uppercase tracking-wider transition-colors text-center",
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

              <h3 className="text-lg font-bold font-display text-[#111111] uppercase tracking-wide">
                {isSourceBased ? 'Registering Source' : 'Adding Data Stream'}
              </h3>
              <p className="text-[#5A5A5A] text-xs mt-1">
                {isSourceBased ? 'Queueing the first run into active Bronze storage segment' : 'Routing file credentials to active Bronze storage segment'}
              </p>

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

              <div className="text-center">
                <button onClick={() => setShowBulkImport(true)} className="btn-ghost btn-sm">
                  <Upload className="w-3.5 h-3.5" /> Bulk Import (YAML)
                </button>
              </div>
            </motion.div>
          )

          /* C. Step 2: Data Purpose (Phase D) */
          : currentStep === 1 ? (
            <motion.div
              key="step-purpose"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="space-y-6 max-w-2xl mx-auto"
            >
              <div className="text-center mb-2">
                <h3 className="text-lg font-bold text-[#111111] uppercase tracking-tight">Data Purpose</h3>
                <p className="text-[#5A5A5A] text-xs">What is this data for? This decides whether it can ever appear in a search/RAG answer.</p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {DATA_PURPOSES.map(p => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => setPurpose(p.id)}
                    className={cn(
                      'p-4 rounded-2xl text-left border-2 transition-all',
                      purpose === p.id
                        ? 'border-[#8A5A00] bg-[#FFF9E8] shadow-sm'
                        : 'border-[#BFA66A] bg-white hover:bg-[#FFFDF3]',
                    )}
                  >
                    <div className={cn('text-xs font-black uppercase tracking-wider mb-1.5', purpose === p.id ? 'text-[#8A5A00]' : 'text-[#111111]')}>
                      {p.name}
                    </div>
                    <div className="text-[10px] text-[#5A5A5A] leading-normal">{p.desc}</div>
                  </button>
                ))}
              </div>
            </motion.div>
          )

          /* D. Step 3: Configure Source — varies by the tile chosen in Step 1 */
          : currentStep === 2 ? (
            <motion.div
              key="step-configure"
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
                <h3 className="text-lg font-bold text-[#111111] uppercase tracking-tight">Configure Source</h3>
              </div>

              <div className="bg-white border-2 border-[#BFA66A] rounded-2xl p-6 space-y-5 shadow-sm">
                {/* Document Hub: 3-way method control */}
                {selectedSource === 'doc' && (
                  <div className="flex flex-col gap-1.5 pb-4 border-b border-[#BFA66A]">
                    <span className="text-[10px] font-black uppercase tracking-widest text-[#111111]">Method</span>
                    <div className="content-card content-card--static inline-flex p-1 shadow-2xs w-fit flex-wrap">
                      {([
                        { id: 'upload', label: 'Upload' },
                        { id: 'url', label: 'From URL' },
                        { id: 'feed', label: 'From Feed-or-API' },
                      ] as { id: DocMethod; label: string }[]).map(m => (
                        <button
                          key={m.id}
                          type="button"
                          onClick={() => setDocMethod(m.id)}
                          className={cn(
                            'px-4 py-2 rounded-xl text-[10px] font-bold uppercase tracking-wide transition-all',
                            docMethod === m.id ? 'bg-[#B88719] text-white shadow-sm' : 'text-[#5A5A5A] hover:bg-[#FAF6ED]'
                          )}
                        >
                          {m.label}
                        </button>
                      ))}
                    </div>
                    <span className="text-[10px] text-[#5A5A5A]">
                      {docMethod === 'upload'
                        ? 'Upload a file now — a one-time Bronze ingestion, same as always.'
                        : docMethod === 'url'
                        ? 'Register a direct file URL (PDF/DOCX/XLSX) as a source — runs immediately, and again on any schedule you set.'
                        : 'Register an RSS feed, REST API, or SDMX endpoint as a source — runs immediately, and again on any schedule you set.'}
                    </span>
                  </div>
                )}

                {/* Upload branch: video/image tiles, or Document Hub's Upload method */}
                {isUpload && (
                  <div className="flex flex-col gap-1.5 pb-4 border-b border-[#BFA66A] last:border-b-0 last:pb-0">
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

                {/* Document Hub "From Feed-or-API": connector pills */}
                {selectedSource === 'doc' && docMethod === 'feed' && (
                  <div className="flex flex-col gap-1.5 pb-2">
                    <span className="text-[10px] font-black uppercase tracking-widest text-[#111111]">Feed Type</span>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {(['rss', 'rest_api', 'sdmx'] as ConnectorType[]).map(type => (
                        <button
                          key={type}
                          type="button"
                          title={CONNECTOR_PILL_DESCRIPTION[type]}
                          onClick={() => setFeedConnector(type)}
                          className={cn(
                            'px-3 py-1.5 rounded-full text-[11px] font-bold border transition-colors',
                            feedConnector === type
                              ? 'bg-[#B88719] border-[#B88719] text-white'
                              : 'bg-white border-[#BFA66A] text-[#5F5F5F] hover:bg-[#FFF9E8]'
                          )}
                        >
                          {CONNECTOR_PILL_LABEL[type]}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* URL field — Document Hub "From URL"/"From Feed-or-API", and Web Portal */}
                {isSourceBased && (
                  <div className="flex flex-col gap-1.5 pb-2">
                    <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">
                      {selectedSource === 'web' ? 'URL Endpoint *' : 'URL *'}
                    </label>
                    <input
                      type="text"
                      placeholder="https://..."
                      value={sourceUrl}
                      onChange={(e) => setSourceUrl(e.target.value)}
                      className="field-input text-xs font-mono"
                    />
                  </div>
                )}

                {/* REST/SDMX auth fields — Document Hub feed method only */}
                {selectedSource === 'doc' && docMethod === 'feed' && (feedConnector === 'rest_api' || feedConnector === 'sdmx') && (
                  <div className="grid grid-cols-1 gap-4">
                    <RestSdmxFields value={restSdmx} onChange={setRestSdmx} />
                  </div>
                )}

                {/* Web Portal: JS-render toggle + recursive crawl options */}
                {selectedSource === 'web' && (
                  <div className="flex flex-col gap-3 pb-2">
                    <label className="flex items-center gap-2 text-xs text-[#3F3F3F]">
                      <input type="checkbox" checked={renderJs} onChange={e => setRenderJs(e.target.checked)} />
                      Render with JavaScript (headless browser — slower, needed for JS-driven pages)
                    </label>
                    <RecursiveCrawlFields value={recursiveCfg} onChange={setRecursiveCfg} />
                  </div>
                )}

                {/* Schedule — every KBSource-creating branch */}
                {isSourceBased && (
                  <SchedulePicker
                    {...schedule}
                    onChange={setSchedule}
                    footnote="This source runs immediately once created, then repeats on this schedule."
                  />
                )}
              </div>
            </motion.div>
          )

          /* E. Step 4: Configure Properties */
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

                {/* 2. Common Name row (Vertical Stack) */}
                <div className="flex flex-col gap-1.5 pb-2">
                  <label className="text-[10px] font-black uppercase tracking-widest text-[#111111]">
                    {selectedSource === 'web' ? 'Web Portal Name *' : isSourceBased ? 'Source Title *' : 'Knowledge Name *'}
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

                {/* 4. Document-specific parameter rows — Upload method only (a
                    scheduled/URL source has no single "author"/"published
                    date" — its config lives on the KBSource itself). */}
                {selectedSource === 'doc' && docMethod === 'upload' && (
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

                {/* 7. Promotion to Gold — only meaningful for a KBSource
                    (something with a URL/schedule to promote automatically). */}
                {isSourceBased && (
                  <div className="pt-2 border-t border-[#BFA66A]">
                    <PromotionModeControl
                      value={promotionModeOverride}
                      effective={effectivePromotionMode}
                      onChange={setPromotionModeOverride}
                      defaultHint={schedule.mode !== 'none' ? 'scheduled sources default to Auto' : 'no schedule defaults to Manual'}
                    />
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
          {currentStep > 0 ? (
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
              <button onClick={() => setCurrentStep(2)} className="btn-gold h-10 px-6 font-black uppercase text-glow">
                Next
                <ArrowRight className="w-4.5 h-4.5 ml-1 inline-block" />
              </button>
            )}

            {currentStep === 2 && (
              <button onClick={goToProperties} className="btn-gold h-10 px-6 font-black uppercase text-glow">
                Next
                <ArrowRight className="w-4.5 h-4.5 ml-1 inline-block" />
              </button>
            )}

            {currentStep === 3 && (
              <button
                onClick={executeAddData}
                className="btn-gold h-10 px-6 font-black uppercase text-glow"
              >
                {isSourceBased ? 'Create Source' : 'Add Data File'}
                <ArrowRight className="w-4.5 h-4.5 ml-1 inline-block" />
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Bulk Import (YAML) modal — paste, parse into an editable preview
          table, confirm; every created row auto-runs immediately. ── */}
      {showBulkImport && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-6" onClick={closeBulkImport}>
          <div className="content-card content-card--static p-6 space-y-4 max-w-[1400px] w-[95vw] max-h-[90vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-bold text-[#1E1B15]">Bulk Import Sources (YAML)</h4>
              <button onClick={closeBulkImport}><XIcon className="w-4 h-4" /></button>
            </div>

            {bulkResult ? (
              <div className="space-y-3">
                <div className="text-sm text-green-700 font-bold">Created {bulkResult.createdCount} source{bulkResult.createdCount === 1 ? '' : 's'} — each is already running its first crawl.</div>
                {bulkResult.skipped.length > 0 && (
                  <div className="text-xs space-y-1">
                    <div className="text-amber-700 font-bold">Skipped: {bulkResult.skipped.length}</div>
                    {bulkResult.skipped.map((s, i) => (
                      <div key={i} className="font-mono text-[#5F5F5F]">— {s.title}: {s.reason}</div>
                    ))}
                  </div>
                )}
                <div className="flex justify-end gap-2">
                  <button className="btn-gold !text-xs" onClick={() => { closeBulkImport(); if (bulkResult.createdCount > 0) onComplete(); }}>Done</button>
                </div>
              </div>
            ) : bulkPreview ? (
              <div className="space-y-3">
                <p className="text-xs text-[#5F5F5F]">
                  Review and edit each row before confirming — every row here becomes a source and starts its first crawl the moment you confirm.
                </p>
                <div className="overflow-x-auto border border-[#EDE6D3] rounded-xl">
                  <table className="w-full text-left border-collapse text-[13px]">
                    <thead>
                      <tr className="bg-[#FFFDF8] text-[10px] font-black text-[#5A4209] uppercase tracking-widest border-b border-[#BFA66A]">
                        <th className="px-3 py-2.5">Title</th>
                        <th className="px-3 py-2.5">Publisher</th>
                        <th className="px-3 py-2.5">Connector</th>
                        <th className="px-3 py-2.5">URL</th>
                        <th className="px-3 py-2.5">Schedule</th>
                        <th className="px-3 py-2.5">Purpose</th>
                        <th className="px-3 py-2.5">Promotion</th>
                        <th className="px-3 py-2.5 w-8" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#EDE6D3]">
                      {bulkPreview.map((row, idx) => (
                        <React.Fragment key={idx}>
                        <tr>
                          <td className="px-3 py-2.5 align-top">
                            <input className="ui-input text-[13px] px-2 py-1.5 w-52" value={row.title} onChange={e => updateBulkRow(idx, { title: e.target.value })} />
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <input className="ui-input text-[13px] px-2 py-1.5 w-44" value={row.publisher || ''} onChange={e => updateBulkRow(idx, { publisher: e.target.value })} />
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <select
                              className="ui-input text-[13px] px-2 py-1.5"
                              value={row.connector_type}
                              onChange={e => updateBulkRow(idx, { connector_type: e.target.value })}
                            >
                              {BULK_CONNECTOR_TYPES.map(type => (
                                <option key={type} value={type}>{CONNECTOR_PILL_LABEL[type]}</option>
                              ))}
                            </select>
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <input className="ui-input text-[13px] px-2 py-1.5 w-full min-w-[22rem] font-mono" value={row.url} onChange={e => updateBulkRow(idx, { url: e.target.value })} />
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            {/* No raw cron shown or typed here — not everyone
                                filling this table knows cron syntax. This is
                                a plain-language readout; "Edit" is the only
                                way to change it, via the same picker Add
                                Source uses. */}
                            <div className="text-[13px] font-medium text-[#111111] max-w-[190px] leading-snug">
                              {cronToHuman(row.cron)}
                            </div>
                            <button
                              type="button"
                              onClick={() => openScheduleEditor(idx, row.cron)}
                              className="mt-1 inline-flex items-center gap-1 text-[11px] font-bold text-[#8A5A00] hover:text-[#B88719]"
                            >
                              <Pencil className="w-3 h-3" /> Edit
                            </button>
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <select
                              className={cn(
                                'ui-input text-[13px] px-2 py-1.5 w-36',
                                !row.purpose && 'border-red-400 bg-red-50',
                              )}
                              value={row.purpose || ''}
                              onChange={e => updateBulkRow(idx, { purpose: e.target.value })}
                            >
                              <option value="" disabled>— Select —</option>
                              {(['production', 'training', 'evaluation'] as DataPurpose[]).map(p => (
                                <option key={p} value={p}>{DATA_PURPOSE_LABEL[p]}</option>
                              ))}
                            </select>
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <select
                              className={cn(
                                'ui-input text-[13px] px-2 py-1.5 w-32',
                                !row.promotion_mode && 'border-red-400 bg-red-50',
                              )}
                              value={row.promotion_mode || ''}
                              onChange={e => updateBulkRow(idx, { promotion_mode: e.target.value })}
                            >
                              <option value="" disabled>— Select —</option>
                              <option value="manual">Manual</option>
                              <option value="auto">Auto</option>
                            </select>
                          </td>
                          <td className="px-3 py-2.5 text-right align-top">
                            <button onClick={() => removeBulkRow(idx)} className="p-1 text-red-500 hover:bg-red-50 rounded"><XIcon className="w-3.5 h-3.5" /></button>
                          </td>
                        </tr>
                        {scheduleEditIdx === idx && (
                          <tr>
                            <td colSpan={8} className="px-4 py-4 bg-[#FFFDF3] border-t border-[#EDE6D3]">
                              <SchedulePicker
                                {...scheduleDraft}
                                onChange={setScheduleDraft}
                                footnote="This source runs immediately once created, then repeats on this schedule."
                                unrepresentableCron={!isCronRepresentable(row.cron) ? row.cron : null}
                              />
                              <div className="flex justify-end gap-2 mt-3">
                                <button className="btn-ghost btn-sm" onClick={() => setScheduleEditIdx(null)}>Cancel</button>
                                <button className="btn-gold !text-xs" onClick={applyScheduleEditor}>Apply</button>
                              </div>
                            </td>
                          </tr>
                        )}
                        </React.Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
                {bulkSkipped.length > 0 && (
                  <div className="text-xs space-y-1">
                    <div className="text-amber-700 font-bold">Skipped while parsing: {bulkSkipped.length}</div>
                    {bulkSkipped.map((s, i) => (
                      <div key={i} className="font-mono text-[#5F5F5F]">— {s.title}: {s.reason}</div>
                    ))}
                  </div>
                )}
                <div className="flex items-center justify-end gap-3">
                  {incompleteBulkRows > 0 && (
                    <span className="text-[11px] font-bold text-red-600">
                      {incompleteBulkRows} row{incompleteBulkRows === 1 ? '' : 's'} missing Purpose or Promotion — fill in every row (highlighted in red) to confirm.
                    </span>
                  )}
                  <button className="btn-ghost btn-sm" onClick={() => setBulkPreview(null)}>Back</button>
                  <button className="btn-gold !text-xs" onClick={confirmBulkImport} disabled={bulkSaving || bulkPreview.length === 0 || incompleteBulkRows > 0}>
                    {bulkSaving ? 'Creating…' : `Confirm & Run ${bulkPreview.length} Source${bulkPreview.length === 1 ? '' : 's'}`}
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-xs text-[#5F5F5F]">
                  Paste a source registry's YAML (a top-level <code>sources:</code> list, e.g. the ANKT registry).
                  <code>http-file</code> / <code>web-crawl</code> / <code>web-crawl-headless</code> / <code>rss</code> / <code>rest-api</code> / <code>sdmx</code> rows
                  are parsed into an editable table below — others (manual-upload, bulk-parquet) are reported as skipped.
                </p>
                <textarea
                  className="ui-input font-mono text-xs h-64 w-full px-3 py-2"
                  value={bulkText}
                  onChange={e => setBulkText(e.target.value)}
                  placeholder="sources:&#10;  - id: my-source&#10;    connector: web-crawl&#10;    landing_url: https://...&#10;    schedule: daily"
                />
                {bulkSkipped.length > 0 && (
                  <div className="text-xs space-y-1">
                    {bulkSkipped.map((s, i) => (
                      <div key={i} className="font-mono text-red-600">— {s.title}: {s.reason}</div>
                    ))}
                  </div>
                )}
                <div className="flex justify-end gap-2">
                  <button className="btn-ghost btn-sm" onClick={closeBulkImport}>Close</button>
                  <button className="btn-gold !text-xs" onClick={handleBulkParse} disabled={bulkParsing || !bulkText.trim()}>
                    {bulkParsing ? 'Parsing…' : 'Parse & Review'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  );
};
