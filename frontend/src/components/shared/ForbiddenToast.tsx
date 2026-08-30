import React, { useState, useEffect } from 'react';
import { ShieldAlert, X } from 'lucide-react';

interface Toast { id: number; message: string; }

export const ForbiddenToast = () => {
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    const handler = (e: Event) => {
      const message = (e as CustomEvent<string>).detail ?? 'Action not permitted for your current role.';
      const id = Date.now();
      setToasts(prev => [...prev, { id, message }]);
      setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 5000);
    };
    window.addEventListener('app:forbidden', handler);
    return () => window.removeEventListener('app:forbidden', handler);
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed top-5 right-5 z-[9999] flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => (
        <div
          key={t.id}
          className="pointer-events-auto flex items-start gap-3 bg-white border border-red-300 shadow-lg rounded-2xl px-4 py-3 max-w-sm animate-in slide-in-from-right-4 duration-200"
        >
          <ShieldAlert className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-bold text-red-700 uppercase tracking-wide mb-0.5">Permission Denied</p>
            <p className="text-xs text-[#2A2A2A] leading-relaxed">{t.message}</p>
          </div>
          <button
            onClick={() => setToasts(prev => prev.filter(x => x.id !== t.id))}
            className="shrink-0 text-slate-400 hover:text-slate-600 transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
};
