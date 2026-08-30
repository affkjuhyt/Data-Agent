import React from 'react';
import { Clock, LogOut, RefreshCw } from 'lucide-react';
import keycloak from '../lib/keycloak';

interface Props {
  email?: string;
  since?: string;
  onRetry: () => void;
}

export const PendingApproval = ({ email, since, onRetry }: Props) => {
  const sinceLabel = since
    ? new Date(since).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
    : null;

  return (
    <div className="h-screen flex items-center justify-center bg-[#FAF6ED]">
      <div className="flex flex-col items-center gap-6 max-w-sm w-full mx-4 text-center">
        {/* Icon */}
        <div className="w-16 h-16 rounded-2xl bg-[#FFF3CD] border border-[#E8C96D] flex items-center justify-center">
          <Clock className="w-8 h-8 text-[#B88719]" />
        </div>

        {/* Heading */}
        <div className="space-y-2">
          <h1 className="text-xl font-bold text-[#111111]">Waiting for admin approval</h1>
          <p className="text-sm text-[#5A5A5A] leading-relaxed">
            Your Microsoft account{email ? ` (${email})` : ''} has been registered.
            An administrator needs to approve access before you can continue.
          </p>
          {sinceLabel && (
            <p className="text-xs text-[#888]">Request submitted {sinceLabel}</p>
          )}
        </div>

        {/* Actions */}
        <div className="flex flex-col gap-2 w-full">
          <button
            onClick={onRetry}
            className="flex items-center justify-center gap-2 w-full px-4 py-2.5 bg-[#111111] text-white rounded-xl text-sm font-semibold hover:bg-[#222] transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
            Check again
          </button>
          <button
            onClick={() => keycloak.logout({ redirectUri: window.location.origin })}
            className="flex items-center justify-center gap-2 w-full px-4 py-2.5 bg-white border border-[#D6C79F] text-[#5A5A5A] rounded-xl text-sm font-medium hover:border-[#B88719] transition-colors"
          >
            <LogOut className="w-4 h-4" />
            Sign out
          </button>
        </div>

        <p className="text-xs text-[#AAA]">
          Contact your administrator if access is not granted within 24 hours.
        </p>
      </div>
    </div>
  );
};
