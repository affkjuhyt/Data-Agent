import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { listDataSpaces } from '../../lib/v1/api';
import { getMe, roleLabel } from '../../lib/v1/me';
import { useV1 } from '../../lib/v1/useV1';
import { MyClients } from './MyClients';
import { KeyValueList } from './parts';

/*
 * "Cài đặt · Hồ sơ" (2324:20173, UC-11-01): who the user is, as the directory says (GET /v1/me),
 * and the Data Spaces they belong to (GET /v1/data-spaces). Read only: the frame hides its Save
 * buttons, and the display format has no editor drawn yet. Below, the API clients the user
 * answers for, where they agree to one granted in their name (MyClients).
 */

// The organisation's locale decides language and number, date and time formats (A1.1).
const LOCALES: Record<string, { label: string; format: string }> = {
  'vi-VN': { label: 'Tiếng Việt', format: '1.234,56 · ngày dd/mm/yyyy · giờ 24h' },
  'en-US': { label: 'English', format: '1,234.56 · date mm/dd/yyyy · 12-hour time' },
};

export default function ProfilePage() {
  const me = useV1(getMe, []);
  const spaces = useV1(listDataSpaces, []);
  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Cài đặt', 'Hồ sơ']}
        title="Hồ sơ"
        description="Thông tin lấy từ Active Directory; chỉ định dạng hiển thị là sửa được ở đây."
      />
      <AsyncRegion state={me} what="hồ sơ" skeletonRows={8}>
        {(data) => {
          const locale = LOCALES[data.locale];
          return (
            <KeyValueList
              rows={[
                { key: 'name', label: 'Họ tên', value: data.display_name },
                { key: 'email', label: 'Email', value: data.email ?? '—' },
                { key: 'unit', label: 'Đơn vị', value: data.department ?? '—' },
                {
                  key: 'role',
                  label: 'Vai trò',
                  value: data.roles.length ? data.roles.map(roleLabel).join('; ') : 'Chưa có vai trò',
                },
                {
                  key: 'spaces',
                  label: 'Data Space',
                  value: spaces.data ? spaces.data.items.map((s) => s.name).join(', ') || '—' : '…',
                },
                { key: 'locale', label: 'Ngôn ngữ', value: locale?.label ?? data.locale },
                { key: 'format', label: 'Định dạng số', value: locale?.format ?? data.locale },
                { key: 'tz', label: 'Múi giờ', value: data.time_zone },
              ]}
            />
          );
        }}
      </AsyncRegion>
      <MyClients />
    </div>
  );
}
