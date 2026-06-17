/**
 * Ingestion Progress Overlay
 * Fixed bottom-right corner, visible on hover
 * Shows real-time progress for documents being ingested
 */

import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { X, Activity, CheckCircle, AlertCircle, ChevronUp } from 'lucide-react';
import { cn } from '../../lib/utils';

interface IngestionProgress {
  dataId: string;
  fileName: string;
  stage: string;
  progress: number;
  status: 'QUEUED' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  logs: string[];
  startTime: Date;
  eventSource?: EventSource;
}

interface IngestionProgressOverlayProps {
  /** Callback when a new ingestion starts */
  onIngestionStart?: (dataId: string) => void;
  /** Callback when ingestion completes */
  onIngestionComplete?: (dataId: string) => void;
}

export const IngestionProgressOverlay: React.FC<IngestionProgressOverlayProps> = ({
  onIngestionStart,
  onIngestionComplete,
}) => {
  const [isHovering, setIsHovering] = useState(false);
  const [ingestions, setIngestions] = useState<Map<string, IngestionProgress>>(new Map());
  const eventSourcesRef = useRef<Map<string, EventSource>>(new Map());

  /**
   * Start monitoring ingestion progress via SSE
   * Called from IngestionWizard when document is uploaded
   */
  const startMonitoring = (dataId: string, fileName: string) => {
    onIngestionStart?.(dataId);

    const progress: IngestionProgress = {
      dataId,
      fileName,
      stage: 'bronze',
      progress: 25,
      status: 'PROCESSING',
      logs: ['Ingestion started...'],
      startTime: new Date(),
    };

    setIngestions(prev => new Map(prev).set(dataId, progress));

    // Open SSE stream to backend
    const eventSource = new EventSource(
      `/api/pipeline/ingestion/documents/${dataId}/progress`
    );

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        setIngestions(prev => {
          const updated = new Map(prev);
          const item = updated.get(dataId);
          if (!item) return updated;

          return updated.set(dataId, {
            ...item,
            stage: data.stage || item.stage,
            logs: [...item.logs, data.message].slice(-10), // Keep last 10 logs
          });
        });

        // Check if complete
        if (data.status === 'PUBLISHED' || data.status === 'FAILED') {
          setIngestions(prev => {
            const updated = new Map(prev);
            const item = updated.get(dataId);
            if (!item) return updated;

            return updated.set(dataId, {
              ...item,
              progress: data.status === 'PUBLISHED' ? 100 : item.progress,
              status: data.status === 'PUBLISHED' ? 'COMPLETED' : 'FAILED',
            });
          });

          eventSource.close();
          eventSourcesRef.current.delete(dataId);
          onIngestionComplete?.(dataId);

          // Auto-remove after 5 seconds if completed
          if (data.status === 'PUBLISHED') {
            setTimeout(() => {
              setIngestions(prev => {
                const updated = new Map(prev);
                updated.delete(dataId);
                return updated;
              });
            }, 5000);
          }
        }
      } catch (error) {
        console.error('Failed to parse SSE data:', error);
      }
    };

    eventSource.onerror = () => {
      eventSource.close();
      eventSourcesRef.current.delete(dataId);
      setIngestions(prev => {
        const updated = new Map(prev);
        const item = updated.get(dataId);
        if (item) {
          updated.set(dataId, {
            ...item,
            status: 'FAILED',
            logs: [...item.logs, 'Connection lost'],
          });
        }
        return updated;
      });
    };

    eventSourcesRef.current.set(dataId, eventSource);
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      eventSourcesRef.current.forEach(es => es.close());
    };
  }, []);

  // Expose startMonitoring via window object so IngestionWizard can call it
  useEffect(() => {
    (window as any).__ingestionProgressOverlay = { startMonitoring };
  }, []);

  if (ingestions.size === 0) return null;

  const activeCount = Array.from(ingestions.values()).filter(
    i => i.status === 'PROCESSING'
  ).length;

  return (
    <div
      className="fixed bottom-6 right-6 z-50"
      onMouseEnter={() => setIsHovering(true)}
      onMouseLeave={() => setIsHovering(false)}
    >
      {/* Trigger Button (Always Visible) */}
      <motion.button
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        exit={{ scale: 0 }}
        className={cn(
          'flex items-center gap-2 px-4 py-2 rounded-full font-semibold shadow-lg',
          'transition-all duration-300',
          isHovering
            ? 'bg-[#B88719] text-white'
            : 'bg-white text-[#B88719] border-2 border-[#B88719] hover:shadow-xl'
        )}
      >
        {activeCount > 0 ? (
          <>
            <motion.div animate={{ rotate: 360 }} transition={{ duration: 2, repeat: Infinity }}>
              <Activity className="w-5 h-5" />
            </motion.div>
            <span className="text-sm">{activeCount} Processing</span>
          </>
        ) : (
          <>
            <CheckCircle className="w-5 h-5" />
            <span className="text-sm">{ingestions.size} Complete</span>
          </>
        )}
        <ChevronUp
          className={cn(
            'w-4 h-4 transition-transform',
            isHovering ? 'rotate-180' : ''
          )}
        />
      </motion.button>

      {/* Expanded Panel (Shows on Hover) */}
      <AnimatePresence>
        {isHovering && (
          <motion.div
            initial={{ opacity: 0, y: 10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.95 }}
            transition={{ duration: 0.2 }}
            className={cn(
              'absolute bottom-16 right-0 w-96 max-h-96 rounded-xl shadow-2xl',
              'bg-white border border-[#BFA66A] overflow-hidden flex flex-col'
            )}
          >
            {/* Header */}
            <div className="bg-gradient-to-r from-[#B88719] to-[#8A5A00] text-white px-4 py-3 flex items-center justify-between">
              <h3 className="font-bold text-sm">Ingestion Progress</h3>
              <span className="text-xs opacity-75">
                {ingestions.size} {ingestions.size === 1 ? 'item' : 'items'}
              </span>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto space-y-2 p-3">
              {Array.from(ingestions.values()).map(item => (
                <ProgressItem key={item.dataId} item={item} />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

const ProgressItem: React.FC<{ item: IngestionProgress }> = ({ item }) => {
  const isActive = item.status === 'PROCESSING';
  const isComplete = item.status === 'COMPLETED';
  const isFailed = item.status === 'FAILED';

  return (
    <div className={cn(
      'p-3 rounded-lg border transition-colors',
      isActive && 'border-[#B88719] bg-[#FAF6ED]',
      isComplete && 'border-green-500 bg-green-50',
      isFailed && 'border-red-500 bg-red-50',
    )}>
      {/* Header */}
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {isActive ? (
            <motion.div animate={{ rotate: 360 }} transition={{ duration: 2, repeat: Infinity }}>
              <Activity className="w-4 h-4 text-[#B88719] flex-shrink-0" />
            </motion.div>
          ) : isComplete ? (
            <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" />
          ) : (
            <AlertCircle className="w-4 h-4 text-red-500 flex-shrink-0" />
          )}
          <span className="text-sm font-semibold text-[#1E1B15] truncate">{item.fileName}</span>
        </div>
        <span className={cn(
          'text-xs font-bold px-2 py-1 rounded ml-2 flex-shrink-0',
          isActive && 'bg-[#B88719] text-white',
          isComplete && 'bg-green-500 text-white',
          isFailed && 'bg-red-500 text-white',
        )}>
          {item.stage}
        </span>
      </div>

      {/* Progress Bar */}
      {isActive && (
        <div className="mb-2">
          <div className="flex justify-between items-center mb-1">
            <p className="text-xs text-[#5A5A5A]">Processing...</p>
            <p className="text-xs font-semibold text-[#1E1B15]">{Math.round(item.progress)}%</p>
          </div>
          <motion.div className="h-2 bg-[#E8DCC8] rounded-full overflow-hidden">
            <motion.div
              className="h-full bg-gradient-to-r from-[#B88719] to-[#8A5A00]"
              initial={{ width: '0%' }}
              animate={{ width: `${item.progress}%` }}
              transition={{ duration: 0.5 }}
            />
          </motion.div>
        </div>
      )}

      {/* Recent Logs */}
      {item.logs.length > 0 && (
        <div className="mt-2 pt-2 border-t border-[#D4C4A8]">
          <p className="text-xs font-bold text-[#1E1B15] mb-1">Activity:</p>
          <div className="text-xs text-[#5A5A5A] space-y-0.5 max-h-12 overflow-y-auto">
            {item.logs.slice(-3).map((log, i) => (
              <p key={i} className="line-clamp-1">
                • {log}
              </p>
            ))}
          </div>
        </div>
      )}

      {/* Time */}
      <div className="mt-2 text-xs text-[#999]">
        Started: {item.startTime.toLocaleTimeString()}
      </div>
    </div>
  );
};

export default IngestionProgressOverlay;
