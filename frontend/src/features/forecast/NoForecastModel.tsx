import { LinkButton } from '../../components/shell/AppLink';
import { EmptyState } from '../../design-system/EmptyState';

/** The forecast screens when the Data Space has no model yet. */
export function NoForecastModel() {
  return (
    <div className="px-da-3xl pt-da-xl">
      <EmptyState
        icon="chart"
        title="Chưa có mô hình dự báo"
        description="Định nghĩa một mô hình trên chỉ số đã chứng nhận. Việc huấn luyện mở ngay sau khi tạo."
        action={
          <LinkButton area="analysis" page="FORECAST_MODEL_NEW" kind="primary">
            Tạo mô hình dự báo
          </LinkButton>
        }
      />
    </div>
  );
}
