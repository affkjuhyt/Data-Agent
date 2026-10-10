import { PageHeader } from '../../components/shell/PageHeader';
import { KeyValueList } from './parts';

/*
 * "Cài đặt · Phím tắt" (2324:20380): the keyboard shortcuts of the app. They are fixed by the
 * client, so the list lives here rather than behind an endpoint.
 */
const SHORTCUTS: [string, string][] = [
  ['⌘K', 'Tìm nhanh, hỏi agent, đi tới màn'],
  ['J / K', 'Xuống / lên trong danh sách'],
  ['Enter', 'Mở mục đang chọn'],
  ['Esc', 'Đóng hộp thoại, bỏ chọn'],
  ['⌘↵', 'Xác nhận lựa chọn trong Cần bạn'],
  ['/', 'Lệnh trong ô hỏi'],
  ['@', 'Nhắc người hoặc agent'],
  ['G rồi H / C / A', 'Đi tới Trang chủ / Cần bạn / Agents'],
];

export default function ShortcutsPage() {
  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Cài đặt', 'Phím tắt']}
        title="Phím tắt"
        description="Phím tắt dùng được ở mọi màn; giữ ? để xem nhanh."
      />
      <KeyValueList
        mono
        rows={SHORTCUTS.map(([keys, action]) => ({
          key: keys,
          label: <kbd className="font-sans">{keys}</kbd>,
          value: action,
        }))}
      />
    </div>
  );
}
