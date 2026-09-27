import { FileText, Globe, Video, Image as ImageIcon } from 'lucide-react';
import { DataPurpose, DATA_PURPOSE_LABEL, DATA_PURPOSE_DESCRIPTION } from '../sources/sourceShared';

export const STEPS = [
  { id: 'source',     label: 'Select Data Source'   },
  { id: 'purpose',    label: 'Data Purpose'         },
  { id: 'configure',  label: 'Configure Source'     },
  { id: 'properties', label: 'Configure Properties' },
];

export const SOURCES = [
  { id: 'video', name: 'Video Asset',  icon: Video,     desc: 'Ingest high-quality streaming payloads (MP4, MKV/AVI)'          },
  { id: 'image', name: 'Image Asset',  icon: ImageIcon, desc: 'Process structural graphic resources (PNG, JPEG, WebP)'          },
  { id: 'doc',   name: 'Document Hub', icon: FileText,  desc: 'Upload a file, or register a URL/API feed (PDF, Word, Excel...)' },
  { id: 'web',   name: 'Web Portal',   icon: Globe,     desc: 'Crawl a URL — one-shot or on a recurring schedule'               },
];

export const DATA_PURPOSES: { id: DataPurpose; name: string; desc: string }[] =
  (['production', 'training', 'evaluation'] as DataPurpose[]).map(id => ({
    id, name: DATA_PURPOSE_LABEL[id], desc: DATA_PURPOSE_DESCRIPTION[id],
  }));

export const formatBytes = (bytes: number, decimals = 1): string => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
};
