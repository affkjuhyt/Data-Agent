"""The eval sets a nightly run is requested for, across organisations.

The nightly scheduler (services/worker_agent/nightly_evals.py, the eval-nightly CronJob) runs for
every organisation while every eval table is org_isolated. eval.nightly_eval_sets() lists only the
active context eval sets of active Data Spaces whose steward is an active user, whom the run is
requested for (each item is answered as its author, eval_runs.run_eval); it reads no item, result
or answer, and only svc_eval may call it. Same text as routines/eval.sql.
"""

from alembic import op

revision = "v3_0016"
down_revision = "v3_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION eval.nightly_eval_sets()
        RETURNS TABLE (org_id uuid, eval_set_id uuid, data_space_id uuid, steward_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
          SELECT s.org_id, s.id, s.data_space_id, d.steward_id
            FROM eval.eval_set s
            JOIN org.data_space d ON d.id = s.data_space_id AND d.org_id = s.org_id
            JOIN org.app_user u ON u.id = d.steward_id AND u.org_id = d.org_id
           WHERE s.subject_type = 'context' AND s.status = 'active' AND d.status = 'active'
             AND u.status = 'active'
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION eval.nightly_eval_sets() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION eval.nightly_eval_sets() TO svc_eval")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS eval.nightly_eval_sets()")
