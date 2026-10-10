import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { getMe } from '../../lib/v1/adminPlatform';
import { useV1 } from '../../lib/v1/useV1';
import { KeyValueList } from './parts';

/*
 * "Cài đặt · Hồ sơ" (2324:20173, UC-11-01): who the user is, as Active Directory says. Read
 * only: the frame hides its Save buttons, and the display format has no editor drawn yet.
 */
export default function ProfilePage() {
  const me = useV1(getMe, []);
  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Cài đặt', 'Hồ sơ']}
        title="Hồ sơ"
        description="Thông tin lấy từ Active Directory; chỉ định dạng hiển thị là sửa được ở đây."
      />
      <AsyncRegion state={me} what="hồ sơ" skeletonRows={8}>
        {(data) => (
          <KeyValueList
            rows={[
              { key: 'name', label: 'Họ tên', value: data.display_name },
              { key: 'email', label: 'Email', value: data.email },
              { key: 'unit', label: 'Đơn vị', value: data.unit_text },
              { key: 'role', label: 'Vai trò', value: data.role_text },
              { key: 'spaces', label: 'Data Space', value: data.data_space_names.join(', ') },
              { key: 'locale', label: 'Ngôn ngữ', value: data.locale_label },
              { key: 'format', label: 'Định dạng số', value: data.format_text },
              { key: 'tz', label: 'Múi giờ', value: data.timezone },
            ]}
          />
        )}
      </AsyncRegion>
    </div>
  );
}
