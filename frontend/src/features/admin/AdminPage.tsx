import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { Badge } from '../../design-system/Badge';
import { LinkCard } from '../../design-system/LinkCard';
import { formatNumber } from '../../lib/formatVN';
import { listModels, listUsers } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Quản trị" (2164:16316, UC-11-01): six entries in a 3-column grid (cards 40px icon slot,
 * Heading 2 title). Each opens the matching admin section. The "Cần bạn để ý" line of the design
 * needs pending access requests and data-subject requests, which have no list endpoint yet.
 */
export default function AdminPage() {
  const { navigate } = useAppState();
  const users = useV1(listUsers, []);
  const models = useV1(listModels, []);

  const people = users.data?.items.filter((u) => u.kind === 'person').length;
  const agents = users.data?.items.filter((u) => u.kind === 'agent').length;
  const worse = models.data?.items.find((m) => (m.latest_eval_delta ?? 0) < 0);

  const open = (section: string) => () => navigate('admin', section);

  return (
    <div className="flex flex-col gap-[20px] px-da-3xl py-da-xl">
      <PageHeader
        title="Quản trị"
        description="Ai được làm gì, agent tuân theo luật nào, dùng mô hình nào và mọi việc được ghi lại ra sao."
      />
      <div className="grid grid-cols-3 gap-da-xl max-[1279px]:grid-cols-2">
        <LinkCard
          icon="lock"
          iconSlot={40}
          titleSize="md"
          title="Người & quyền"
          onOpen={open('USERS')}
        >
          {people !== undefined && (
            <span className="text-muted-foreground da-body-small">
              {formatNumber(people)} người · {agents} agent
            </span>
          )}
        </LinkCard>
        <LinkCard
          icon="sliders"
          iconSlot={40}
          titleSize="md"
          title="Chính sách & quy tắc"
          onOpen={open('POLICIES')}
        >
          <span className="text-muted-foreground da-body-small">
            Chính sách dữ liệu và hành vi của agent
          </span>
        </LinkCard>
        <LinkCard
          icon="agent"
          iconSlot={40}
          titleSize="md"
          title="Mô hình AI"
          onOpen={open('MODELS')}
        >
          {models.data && (
            <span className="text-muted-foreground da-body-small">
              {models.data.items.length} loại tác vụ · chạy nội bộ
            </span>
          )}
          {worse && (
            <Badge tone="warning">
              “{worse.task}” giảm chất lượng {Math.abs(worse.latest_eval_delta ?? 0)} điểm
            </Badge>
          )}
        </LinkCard>
        <LinkCard
          icon="database"
          iconSlot={40}
          titleSize="md"
          title="Tích hợp"
          onOpen={open('INTEGRATIONS')}
        >
          <span className="text-muted-foreground da-body-small">Teams, email, SSO, API, MCP</span>
        </LinkCard>
        <LinkCard
          icon="file"
          iconSlot={40}
          titleSize="md"
          title="Kiểm toán"
          onOpen={open('AUDIT')}
        >
          <span className="text-muted-foreground da-body-small">
            Ai đã làm gì, khi nào, thay ai
          </span>
        </LinkCard>
        <LinkCard
          icon="eye"
          iconSlot={40}
          titleSize="md"
          title="Quyền riêng tư"
          onOpen={open('PRIVACY')}
        >
          <span className="text-muted-foreground da-body-small">Yêu cầu của chủ thể dữ liệu</span>
        </LinkCard>
      </div>
    </div>
  );
}
