"""Exec handover completion (UC-16-06, BR-195), found missing by the api tests of 12/10/2026.

- handover_complete sets sealed_read_until when a vault is sealed (the contract's term when the
  vault carries one, else 24 months), and seals the vault when a selective handover completes;
  without it the owner's read-only term and the shred after it never happened.
- exec.handover_accept lets the successor complete a selective handover once every item is
  imported: the successor can neither read nor update exec.handover, so the api could not.
- exec.successor_has_active_vault answers the UC-16-06 precondition without letting svc_exec see
  another leader's vault.

Fresh installs get the same functions from the generated dataagent.sql (routines/exec.sql); this
revision replaces them in place, so both paths end equal.

Revision ID: v3_0007
Revises: v3_0006
"""

from alembic import op

revision = "v3_0007"
down_revision = "v3_0006"
branch_labels = None
depends_on = None

ROUTINES = r"""
SET LOCAL ROLE da_owner;
CREATE OR REPLACE FUNCTION exec.handover_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  v exec.vault%ROWTYPE;
BEGIN
  IF NEW.kind IN ('seal', 'selective_handover') THEN
    -- A selective handover ends sealed too (UC-16-06 postcondition). The owner reads the sealed
    -- vault until sealed_read_until: the contract's term when the vault carries one, else the
    -- default 24 months (UC-16-06 step 3); past it the worker shreds (reason sealed_expired).
    UPDATE exec.vault SET status = 'sealed', sealed_at = coalesce(sealed_at, now()),
      sealed_read_until = coalesce(sealed_read_until, (now() + interval '24 months')::date),
      contract_ref = coalesce(NEW.contract_ref, contract_ref)
    WHERE id = NEW.vault_id AND owner_id = NEW.owner_id AND org_id = NEW.org_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Yêu cầu rời vị trí không khớp kho của chủ kho (BR-195)' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.kind = 'shred' THEN
    UPDATE exec.vault SET status = 'shredded', shredded_at = now()
    WHERE id = NEW.vault_id AND owner_id = NEW.owner_id AND org_id = NEW.org_id
    RETURNING * INTO v;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Yêu cầu rời vị trí không khớp kho của chủ kho (BR-195)' USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO exec.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'vault', NEW.vault_id, 'exec.vault.shred_requested',
            jsonb_build_object('vault_id', NEW.vault_id, 'handover_id', NEW.id, 'owner_id', NEW.owner_id,
                               'transit_key_name', v.transit_key_name,
                               'transit_key_version', v.transit_key_version));
  END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION exec.handover_accept(p_handover_id uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  me uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
  org uuid := NULLIF(current_setting('app.org_id', true), '')::uuid;
  h exec.handover%ROWTYPE;
BEGIN
  SELECT * INTO h FROM exec.handover
   WHERE id = p_handover_id AND org_id = org AND successor_id = me
     AND kind = 'selective_handover'
   FOR UPDATE;
  IF NOT FOUND OR me IS NULL THEN
    RETURN NULL;
  END IF;
  IF h.status NOT IN ('approved', 'in_progress') OR h.items_total = 0
     OR EXISTS (SELECT 1 FROM exec.handover_item i
                 WHERE i.handover_id = h.id AND i.imported_at IS NULL) THEN
    RETURN h.status;
  END IF;
  UPDATE exec.handover SET status = 'completed', completed_at = now(), updated_at = now()
   WHERE id = h.id;
  RETURN 'completed';
END $$;
REVOKE EXECUTE ON FUNCTION exec.handover_accept(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION exec.handover_accept(uuid) TO svc_exec;

-- UC-16-06 precondition: the successor has an active vault. svc_exec sees only the caller's own
-- vault, so this answers yes or no for one user of the caller's organisation, nothing more.
CREATE OR REPLACE FUNCTION exec.successor_has_active_vault(p_successor_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT EXISTS (SELECT 1 FROM exec.vault v
                  WHERE v.owner_id = p_successor_id AND v.status = 'active'
                    AND v.org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
$$;
REVOKE EXECUTE ON FUNCTION exec.successor_has_active_vault(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION exec.successor_has_active_vault(uuid) TO svc_exec;
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(ROUTINES)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS exec.handover_accept(uuid)")
    op.execute("DROP FUNCTION IF EXISTS exec.successor_has_active_vault(uuid)")
