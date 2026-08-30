import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'motion/react';
import { X, Info, History, Shield, Maximize2, Minimize2, GripVertical } from 'lucide-react';
import { cn } from '../../lib/utils';

/* ─── Drawer size system ──────────────────────────────────────────── */
type DrawerSizeKey =
  | 'compact' | 'standard' | 'wide' | 'xwide' | 'workspace'
  /* legacy aliases */
  | 'sm' | 'md' | 'lg' | 'xl' | 'full';

interface SizeRange { min: number; default: number; max: number }

const SIZE_CONFIG: Record<'compact' | 'standard' | 'wide' | 'xwide' | 'workspace', SizeRange> = {
  compact:   { min: 420, default: 480,  max: 520 },
  standard:  { min: 560, default: 680,  max: 760 },
  wide:      { min: 760, default: 920,  max: 1080 },
  xwide:     { min: 960, default: 1100, max: 1280 },
  workspace: { min: 960, default: 1280, max: 1440 },
};

const SIZE_ALIAS: Record<'sm' | 'md' | 'lg' | 'xl' | 'full', keyof typeof SIZE_CONFIG> = {
  sm:   'compact',
  md:   'standard',
  lg:   'wide',
  xl:   'xwide',
  full: 'workspace',
};

const resolveSize = (s: DrawerSizeKey): keyof typeof SIZE_CONFIG => {
  return (SIZE_ALIAS as any)[s] ?? (s as keyof typeof SIZE_CONFIG);
};

const useViewportWidth = () => {
  const [w, setW] = useState(() => (typeof window !== 'undefined' ? window.innerWidth : 1280));
  useEffect(() => {
    const onResize = () => setW(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return w;
};

const computeDefaultWidth = (sizeKey: keyof typeof SIZE_CONFIG, vw: number): number => {
  if (sizeKey === 'xwide')     return Math.min(Math.round(vw * 0.72), SIZE_CONFIG.xwide.max);
  if (sizeKey === 'workspace') return Math.min(Math.round(vw * 0.85), SIZE_CONFIG.workspace.max);
  return SIZE_CONFIG[sizeKey].default;
};

interface DetailDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  icon?: any;
  size?: DrawerSizeKey;
  tabs?: { id: string; label: string; icon?: any }[];
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
  /* Persistent key for remembering user-resized width */
  persistKey?: string;
  fixedHeight?: boolean;
}

export const DetailDrawer = ({
  isOpen, onClose, title, subtitle, children, footer,
  icon: Icon = Info, size = 'standard', tabs, activeTab, onTabChange,
  persistKey,
}: DetailDrawerProps) => {
  const sizeKey   = resolveSize(size);
  const sizeRange = SIZE_CONFIG[sizeKey];
  const vw        = useViewportWidth();
  const isMobile  = vw < 768;
  const storageKey = persistKey ? `drawer-w:${persistKey}` : `drawer-w:${sizeKey}`;

  const [isFullscreen, setIsFullscreen] = useState(false);
  const [width, setWidth] = useState<number>(() => {
    if (typeof window === 'undefined') return sizeRange.default;
    const stored = parseInt(localStorage.getItem(storageKey) || '', 10);
    if (!Number.isNaN(stored) && stored >= sizeRange.min && stored <= sizeRange.max) return stored;
    return computeDefaultWidth(sizeKey, window.innerWidth);
  });

  /* Recompute defaults whenever the size key changes (e.g., same drawer reopened with new size) */
  useEffect(() => {
    const stored = parseInt(localStorage.getItem(storageKey) || '', 10);
    if (!Number.isNaN(stored) && stored >= sizeRange.min && stored <= sizeRange.max) {
      setWidth(stored);
    } else {
      setWidth(computeDefaultWidth(sizeKey, vw));
    }
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [sizeKey, storageKey]);

  /* Clamp width on viewport resize */
  useEffect(() => {
    const maxAllowed = Math.min(sizeRange.max, vw - 40);
    if (width > maxAllowed) setWidth(maxAllowed);
  }, [vw, width, sizeRange.max]);

  /* Resize-handle drag */
  const isResizing = useRef(false);
  const startResize = useCallback((e: React.MouseEvent) => {
    if (isFullscreen || isMobile) return;
    e.preventDefault();
    isResizing.current = true;
    const startX = e.clientX;
    const startWidth = width;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMove = (ev: MouseEvent) => {
      if (!isResizing.current) return;
      const delta = startX - ev.clientX;
      const maxAllowed = Math.min(sizeRange.max, window.innerWidth - 40);
      const next = Math.max(sizeRange.min, Math.min(maxAllowed, startWidth + delta));
      setWidth(next);
    };
    const onUp = () => {
      isResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      try { localStorage.setItem(storageKey, String(width)); } catch {}
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [width, sizeRange.min, sizeRange.max, isFullscreen, isMobile, storageKey]);

  /* Persist width when user finishes dragging or toggles fullscreen */
  useEffect(() => {
    if (!isResizing.current && !isFullscreen) {
      try { localStorage.setItem(storageKey, String(width)); } catch {}
    }
  }, [width, storageKey, isFullscreen]);

  /* Computed visual width — mobile is 100vw, fullscreen is viewport minus margins */
  const renderedWidth = isMobile
    ? '100vw'
    : isFullscreen
      ? 'calc(100vw - 24px)'
      : `${width}px`;

  return createPortal(
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/45 backdrop-blur-[2px] z-[100]"
          />

          {/* Sheet */}
          <motion.div
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', damping: 28, stiffness: 220 }}
            style={{ width: renderedWidth }}
            className={cn(
              '!fixed top-0 bottom-0 right-0',
              !isMobile && 'lg:top-3 lg:bottom-3 lg:right-3',
              'drawer-shell',
              'z-[101] flex flex-col',
              isMobile ? 'rounded-none' : 'rounded-t-2xl lg:rounded-2xl',
              'overflow-hidden',
              'max-w-full',
            )}
          >
            {/* Resize handle (desktop only, hidden when fullscreen) */}
            {!isFullscreen && !isMobile && (
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize drawer"
                onMouseDown={startResize}
                className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-[#B88719]/30 active:bg-[#B88719]/50 transition-colors z-10 group"
              >
                <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-12 bg-[#BFA66A] rounded-r-full opacity-0 group-hover:opacity-100 transition-opacity" />
                <GripVertical className="absolute left-[-3px] top-1/2 -translate-y-1/2 w-3 h-3 text-[#8A5A00] opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
            )}

            {/* ── Black Header ── */}
            <div className="drawer-header px-6 pt-5 pb-0 shrink-0 min-h-[64px]">
              <div className="flex items-center justify-between gap-3 mb-3">
                <div className="flex items-center gap-3 min-w-0">
                  <div className="p-2.5 bg-[#B88719] rounded-xl border border-[#8A5A00] shrink-0">
                    <Icon className="w-5 h-5 text-white" />
                  </div>
                  <div className="min-w-0">
                    <h2 className="drawer-title text-lg font-bold tracking-tight font-display truncate">{title}</h2>
                    {subtitle && (
                      <p className="drawer-subtitle text-[12px] font-medium leading-snug truncate">{subtitle}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  {!isMobile && (
                    <button
                      onClick={() => setIsFullscreen(!isFullscreen)}
                      aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
                      className="drawer-ctrl w-8 h-8 hidden lg:flex items-center justify-center rounded-lg transition-all"
                    >
                      {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
                    </button>
                  )}
                  <button
                    onClick={onClose}
                    aria-label="Close drawer"
                    className="drawer-ctrl w-8 h-8 flex items-center justify-center rounded-lg transition-all"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {tabs && tabs.length > 0 && (
                <div className="flex gap-1 -mb-px relative z-10 overflow-x-auto no-scrollbar">
                  {tabs.map(tab => (
                    <button
                      key={tab.id}
                      onClick={() => onTabChange?.(tab.id)}
                      aria-current={activeTab === tab.id ? 'page' : undefined}
                      className={cn('drawer-tab shrink-0', activeTab === tab.id && 'active')}
                    >
                      {tab.icon && <tab.icon className="w-3.5 h-3.5" />}
                      {tab.label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* ── White Body ── */}
            <div className="drawer-body flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar">
              {children}
            </div>

            {/* ── Pale Yellow Footer ── */}
            <div className="drawer-footer px-6 py-3.5 flex items-center justify-between shrink-0 gap-3 flex-wrap">
              <div className="flex gap-2 flex-wrap">
                {footer ?? (
                  <>
                    <button className="btn-secondary">
                      <History className="w-3.5 h-3.5" /> History
                    </button>
                    <button className="btn-secondary">
                      <Shield className="w-3.5 h-3.5" /> Audit
                    </button>
                  </>
                )}
              </div>
              {!footer && (
                <span className="text-[10px] text-[#5A5A5A] font-mono whitespace-nowrap">
                  TRACE: {Math.random().toString(36).substring(7).toUpperCase()}
                </span>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>,
    document.body,
  );
};
