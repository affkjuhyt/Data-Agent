import { useState } from 'react';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { type EntityRelationshipRow, retireRelationship } from '../../lib/v1/semanticLayer';
import { useV1Mutation } from '../../lib/v1/useV1';

/*
 * The joins of the selected entity, one row each, and the way to take back an active one: an
 * approved join could not be undone, so a role accepted by mistake (a ship-date key beside the
 * order date) left the compiler two paths to the calendar. "Gỡ" asks for it with a reason; the
 * join stays active until someone else accepts the proposal in Cần bạn.
 */
export function RelationshipList({ rows }: { rows: EntityRelationshipRow[] }) {
  const toast = useToast();
  const [open, setOpen] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const retire = useV1Mutation(
    (row: EntityRelationshipRow) => retireRelationship(row.id, reason.trim()),
    {
      onSuccess: (_, row) => {
        toast({ tone: 'success', message: `Đã gửi đề xuất gỡ ${row.text}` });
        setOpen(null);
        setReason('');
      },
    },
  );

  if (rows.length === 0) return null;
  return (
    <>
      <h3 className="text-muted-foreground da-overline">Quan hệ</h3>
      <ul className="flex flex-col gap-da-sm da-body-small">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-col gap-da-xs">
            <div className="flex items-center gap-da-sm">
              <span className="min-w-0 flex-1 text-foreground da-mono">{row.text}</span>
              <Badge tone="neutral">{row.status}</Badge>
              {row.active && open !== row.id && (
                <Button
                  kind="ghost"
                  size="sm"
                  onClick={() => {
                    setOpen(row.id);
                    setReason('');
                    retire.reset();
                  }}
                >
                  Gỡ
                </Button>
              )}
            </div>
            {open === row.id && (
              <div className="flex flex-col gap-da-xs">
                <Textarea
                  aria-label={`Lý do gỡ ${row.text}`}
                  placeholder="Vì sao gỡ quan hệ này"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  rows={2}
                />
                {retire.error && (
                  <Notice tone="error">Không gửi được: {retire.error.message}</Notice>
                )}
                <div className="flex gap-da-sm">
                  <Button
                    size="sm"
                    disabled={retire.pending || reason.trim().length === 0}
                    onClick={() => retire.run(row)}
                  >
                    Gửi đề xuất gỡ
                  </Button>
                  <Button kind="ghost" size="sm" onClick={() => setOpen(null)}>
                    Huỷ
                  </Button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}
