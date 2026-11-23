import { PageHeader } from '../../components/shell/PageHeader';
import { KeyValueList } from './parts';

/*
 * "Cài đặt · Phím tắt" (2324:20380): the keyboard shortcuts of the app. They are fixed by the
 * client, so the list lives here rather than behind an endpoint. Only keys something handles are
 * listed: ⌘K (AppShell), Enter on a focused row or in the composer, Esc in dialogs and drawers,
 * "/" commands in the ask box. The frame's J/K, "G then H/C/A", ⌘↵ in Cần bạn, @ mentions and
 * "hold ?" have no handler.
 */
const SHORTCUTS: [string, string][] = [
  ['⌘K', 'Tìm nhanh, hỏi agent, đi tới màn'],
  ['Enter', 'Mở dòng đang chọn trong danh sách; gửi câu hỏi'],
  ['Shift + Enter', 'Xuống dòng trong ô hỏi'],
  ['Esc', 'Đóng hộp thoại, ngăn kéo'],
  ['/', 'Lệnh trong ô hỏi'],
];

export default function ShortcutsPage() {
  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Cài đặt', 'Phím tắt']}
        title="Phím tắt"
        description="Phím tắt có trong ứng dụng."
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
