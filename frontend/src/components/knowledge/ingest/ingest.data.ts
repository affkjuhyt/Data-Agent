import { FileText, Globe, Video, Image as ImageIcon } from 'lucide-react';

export const STEPS = [
  { id: 'source',   label: 'Select Source Type'   },
  { id: 'metadata', label: 'Configure Properties' },
];

export const SOURCES = [
  { id: 'video', name: 'Video Asset',  icon: Video,     desc: 'Ingest high-quality streaming payloads (MP4, MKV/AVI)'          },
  { id: 'image', name: 'Image Asset',  icon: ImageIcon, desc: 'Process structural graphic resources (PNG, JPEG, WebP)'          },
  { id: 'doc',   name: 'Document Hub', icon: FileText,  desc: 'Extract semantic elements & text (PDF, Word, Markdown)'          },
  { id: 'web',   name: 'Web Portal',   icon: Globe,     desc: 'Crawl absolute URL endpoints, HTML markup and assets'            },
];

export const formatBytes = (bytes: number, decimals = 1): string => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
};
