import { useRef, useState, type DragEvent, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  commitUpload,
  createUpload,
  type UploadAnalysis,
  type UploadColumn,
  type UploadColumnType,
} from '../../lib/v1/sources';

/*
 * "P04 · Tải tệp · Kiểm tra cột" (2282:17099, UC-04-08): after a CSV/Excel file is chosen, the
 * agent's reading of it — which row holds the headers, each column's name, type, unit, an
 * example and whether it is personal data — editable before the table is created; on the right
 * where it goes. The choosing step has no frame: it is a plain drop zone.
 */
const TYPES: { value: UploadColumnType; label: string }[] = [
  { value: 'string', label: 'Chuỗi' },
  { value: 'number', label: 'Số' },
  { value: 'percent', label: 'Phần trăm' },
  { value: 'date', label: 'Ngày' },
  { value: 'boolean', label: 'Đúng/sai' },
];

export default function UploadPage() {
  const { navigate } = useAppState();
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [analysis, setAnalysis] = useState<UploadAnalysis | null>(null);
  const [columns, setColumns] = useState<UploadColumn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const read = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const result = await createUpload({ file_name: file.name, size_bytes: file.size });
      setAnalysis(result);
      setColumns(result.columns);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    if (!analysis) return;
    setBusy(true);
    try {
      const { table } = await commitUpload(analysis.id, columns);
      toast({ tone: 'success', message: `Đã tạo bảng ${table}. Job nạp đang chạy.` });
      navigate('data', 'JOBS');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setAnalysis(null);
    setColumns([]);
    setError(null);
    inputRef.current?.click();
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    void read(event.dataTransfer.files[0]);
  };

  const picker = (
    <input
      ref={inputRef}
      type="file"
      accept=".csv,.xlsx,.xls"
      className="sr-only"
      aria-label="Chọn tệp CSV hoặc Excel"
      onChange={(e) => {
        void read(e.target.files?.[0]);
        e.target.value = '';
      }}
    />
  );

  if (!analysis)
    return (
      <div className="flex flex-col">
        <PageHeader
          bordered
          breadcrumb={['Dữ liệu', 'Tải tệp']}
          title="Tải tệp"
          description="Tải CSV hoặc Excel để dùng như một bảng. Agent đọc tệp, nhận ra dòng tiêu đề và kiểu từng cột để bạn kiểm tra trước khi tạo bảng."
        />
        <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl">
          {error && <Notice tone="error">{error}</Notice>}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={cn(
              'flex flex-col items-center gap-da-lg rounded-lg border border-dashed px-da-2xl py-da-3xl text-center',
              dragging ? 'border-primary bg-accent' : 'border-border-strong bg-card',
            )}
          >
            <Icon name="upload" className="text-primary" />
            <p className="text-foreground da-body">Kéo tệp vào đây, hoặc chọn từ máy.</p>
            <p className="text-muted-foreground da-caption">CSV, XLSX, XLS</p>
            {picker}
            <Button kind="primary" loading={busy} onClick={() => inputRef.current?.click()}>
              Chọn tệp
            </Button>
          </div>
        </div>
      </div>
    );

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Tải tệp']}
        title={analysis.file_name}
        description={`Trang tính “${analysis.sheet}” · ${formatNumber(analysis.row_count)} dòng · Agent nhận ra dòng tiêu đề ở dòng ${analysis.header_row}${
          analysis.header_row > 1 ? ` (dòng 1–${analysis.header_row - 1} là tiêu đề báo cáo)` : ''
        }`}
        actions={
          <>
            {picker}
            <Button kind="ghost" onClick={reset}>
              Chọn tệp khác
            </Button>
            <Button
              kind="primary"
              loading={busy}
              disabled={columns.some((c) => !c.name.trim())}
              onClick={() => void create()}
            >
              Tạo bảng
            </Button>
          </>
        }
      />
      <div className="flex flex-1 max-[1023px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col gap-da-lg pl-da-3xl pr-da-2xl pt-da-xl">
          {error && <Notice tone="error">{error}</Notice>}
          <h2 className="text-foreground da-heading-3">Cột trong tệp</h2>
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="w-[170px] px-da-lg py-da-md font-normal">Cột trong tệp</th>
                <th className="w-[180px] px-da-md py-da-md font-normal">Tên cột</th>
                <th className="w-[140px] px-da-md py-da-md font-normal">Kiểu</th>
                <th className="w-[100px] px-da-md py-da-md font-normal max-[1279px]:hidden">
                  Đơn vị
                </th>
                <th className="px-da-md py-da-md font-normal">Ví dụ</th>
                <th className="w-[80px] px-da-md py-da-md font-normal">Cá nhân</th>
              </tr>
            </thead>
            <tbody>
              {columns.map((column, i) => {
                const update = (patch: Partial<UploadColumn>) =>
                  setColumns((list) => list.map((c, j) => (j === i ? { ...c, ...patch } : c)));
                return (
                  <tr
                    key={column.header}
                    className={cn('border-b border-border', column.issue && 'bg-warning-subtle')}
                  >
                    <td className="px-da-lg py-da-md text-foreground da-body-small">
                      {column.header}
                    </td>
                    <td className="px-da-md py-da-md">
                      <Input
                        aria-label={`Tên cột cho “${column.header}”`}
                        value={column.name}
                        onChange={(e) =>
                          update({
                            name: e.target.value.replace(/[^a-z0-9_]/gi, '_').toLowerCase(),
                          })
                        }
                      />
                    </td>
                    <td className="px-da-md py-da-md">
                      <Select<UploadColumnType>
                        label={`Kiểu của “${column.header}”`}
                        value={column.type}
                        onChange={(type) => update({ type })}
                        options={TYPES}
                      />
                    </td>
                    <td className="px-da-md py-da-md text-muted-foreground da-body-small max-[1279px]:hidden">
                      {column.unit ?? '—'}
                    </td>
                    <td
                      className={cn(
                        'px-da-md py-da-md da-body-small',
                        column.issue ? 'text-warning' : 'text-muted-foreground',
                      )}
                    >
                      {column.example}
                      {column.issue && ` · ${column.issue}`}
                    </td>
                    <td
                      className={cn(
                        'px-da-md py-da-md da-body-small',
                        column.personal ? 'text-foreground' : 'text-muted-foreground',
                      )}
                    >
                      {column.personal ? 'Có' : 'Không'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {analysis.issue_note && <p className="text-warning da-caption">{analysis.issue_note}</p>}
        </div>
        <aside className="flex w-[360px] shrink-0 flex-col gap-da-lg border-l border-border bg-background px-da-2xl pt-[20px] max-[1023px]:w-full max-[1023px]:border-l-0 max-[1023px]:border-t max-[1023px]:pb-da-xl">
          <h2 className="text-foreground da-heading-3">Đưa vào đâu</h2>
          <Fact label="Bảng đích">
            {analysis.target.table}
            {analysis.target.is_new ? ' (mới)' : ''}
          </Fact>
          <Fact label="Data Space">{analysis.target.data_space_name}</Fact>
          <Fact label="Khi tải lại tệp cùng tên">{analysis.target.on_reupload}</Fact>
          <Fact label="Hết hạn">{analysis.target.expires ?? 'Không'}</Fact>
          <h2 className="text-foreground da-heading-3">Lưu ý</h2>
          <p className="text-foreground da-body-small">
            Dữ liệu tải tay có mức thẩm quyền thấp nhất: dùng để phân tích và đặt chỉ tiêu, không
            dùng cho báo cáo nộp cơ quan quản lý. Khi trùng với số từ hệ thống nguồn, số từ nguồn
            được ưu tiên.
          </p>
          <p className="text-muted-foreground da-caption">UC-04-08</p>
        </aside>
      </div>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-[2px]">
      <span className="text-muted-foreground da-caption">{label}</span>
      <span className="text-foreground da-body-small">{children}</span>
    </div>
  );
}
