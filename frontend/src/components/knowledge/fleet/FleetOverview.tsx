import React, { useState, useEffect } from 'react';
import { FileText, Globe, Film, Server, Database, Network, GitBranch, AlertCircle, Loader2 } from 'lucide-react';
import { apiGet } from '../../../lib/apiClient';
import { FleetStats } from './fleet.data';

interface StatCardProps {
  icon: React.ElementType;
  label: string;
  value: number;
  sub: string;
  accent: string;
  loading: boolean;
}

const StatCard = ({ icon: Icon, label, value, sub, accent, loading }: StatCardProps) => (
  <div className="bg-white border border-[#BFA66A]/20 rounded-3xl p-6 flex flex-col gap-4">
    <div className="flex items-center justify-between">
      <span className="text-[10px] font-black uppercase tracking-widest text-[#6B6B6B]">{label}</span>
      <div className={`w-8 h-8 rounded-xl flex items-center justify-center ${accent}`}>
        <Icon className="w-4 h-4" />
      </div>
    </div>
    {loading ? (
      <Loader2 className="w-5 h-5 text-[#BFA66A] animate-spin" />
    ) : (
      <div>
        <div className="text-3xl font-bold text-[#2A2A2A] tracking-tight">{value}</div>
        <div className="text-[10px] text-[#6B6B6B] mt-1 font-medium">{sub}</div>
      </div>
    )}
  </div>
);

export const FleetOverview = () => {
  const [stats, setStats] = useState<FleetStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGet<FleetStats>('/api/fleet/stats')
      .then(data => setStats(data))
      .finally(() => setLoading(false));
  }, []);

  const unresolvedCount = stats?.unresolved_conflict_batches ?? 0;

  return (
    <div className="space-y-8">

      {/* ── Knowledge Content ── */}
      <div>
        <p className="text-[10px] font-black uppercase tracking-widest text-[#6B6B6B] mb-4">
          Gold Layer · Knowledge Content
        </p>
        <div className="grid grid-cols-4 gap-5">
          <StatCard
            icon={FileText}
            label="Documents"
            value={stats?.content.documents ?? 0}
            sub="Doc / PDF / MD sources"
            accent="bg-[#BFA66A]/10 text-[#BFA66A]"
            loading={loading}
          />
          <StatCard
            icon={Globe}
            label="Web Sources"
            value={stats?.content.web ?? 0}
            sub="Scraped web pages"
            accent="bg-blue-50 text-blue-500"
            loading={loading}
          />
          <StatCard
            icon={Film}
            label="Media"
            value={stats?.content.media ?? 0}
            sub="Audio / video files"
            accent="bg-purple-50 text-purple-500"
            loading={loading}
          />
          <StatCard
            icon={Server}
            label="Warehouses"
            value={stats?.content.warehouses ?? 0}
            sub="Snowflake / Databricks"
            accent="bg-emerald-50 text-emerald-500"
            loading={loading}
          />
        </div>
      </div>

      {/* ── Infrastructure & Conflicts ── */}
      <div>
        <p className="text-[10px] font-black uppercase tracking-widest text-[#6B6B6B] mb-4">
          Infrastructure & Conflicts
        </p>
        <div className="grid grid-cols-4 gap-5">
          <StatCard
            icon={Database}
            label="Qdrant Collections"
            value={stats?.qdrant_collections ?? 0}
            sub="Vector store collections"
            accent="bg-[#BFA66A]/10 text-[#BFA66A]"
            loading={loading}
          />
          <StatCard
            icon={Network}
            label="Neo4j Nodes"
            value={stats?.neo4j_nodes ?? 0}
            sub="Knowledge graph entities"
            accent="bg-blue-50 text-blue-500"
            loading={loading}
          />
          <StatCard
            icon={GitBranch}
            label="Relationships"
            value={stats?.neo4j_relationships ?? 0}
            sub="Graph edge connections"
            accent="bg-purple-50 text-purple-500"
            loading={loading}
          />
          <StatCard
            icon={AlertCircle}
            label="Unresolved Conflicts"
            value={unresolvedCount}
            sub="Batches with pending items"
            accent={unresolvedCount > 0 ? 'bg-red-50 text-red-500' : 'bg-emerald-50 text-emerald-500'}
            loading={loading}
          />
        </div>
      </div>

    </div>
  );
};
