"""Remove the industry packs (decided 01/11/2026: Data Agent is domain-general, there are no packs).

What a cluster migrated before this revision still has, and the generated dataagent.sql no
longer does:
- schema pack (15 tables and its outbox) and the roles svc_pack, pack_read;
- the columns that only pointed into it: org.organization.industry_code,
  org.data_space.pack_install_id, context.semantic_entity.canonical_entity_id,
  context.metric.pack_metric_id, context.metric_version.override_id,
  context.data_product.pack_version_id, eval.error_book_entry.pack_contribution and
  pack_proposal_ref (dropping a column drops the CHECKs that name it);
- the CHECK values 'pack' (an origin, a memory source, an eval set subject, a registry source),
  the fix kind 'pack_version' and the proposal kinds pack_activate, pack_upgrade, metric_override;
- the tools pack.suggest_mappings and pack.propose_decisions of agent.tool_def, and the
  capability pack_author from the roles that carry it.

No code path wrote a pack value outside schema pack, so no row holds one; if one does, adding the
narrower CHECK fails and the migration stops rather than rewriting the row. Comments follow the
generated DDL. Idempotent: on a fresh install, whose baseline never had packs, only the comments
are written again.

Revision ID: v3_0017
Revises: v3_0016
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0017"
down_revision = "v3_0016"
branch_labels = None
depends_on = None

# Unnamed constraints of the baseline are found by their definition, as in v3_0011.
REPLACE_CHECK = """
DO $$
DECLARE c record; replaced boolean := false;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = '{table}'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) ~ '{pattern}'
  LOOP
    EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', c.conname);
    replaced := true;
  END LOOP;
  IF replaced THEN
    ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({check});
  END IF;
END $$;
"""

# (table, column, pattern that only the old CHECK matches, new CHECK)
CHECKS = (
    ("governance.behavior_policy", "origin", "'pack'", "origin IN ('manual','hitl')"),
    (
        "context.relationship",
        "origin",
        "'pack'",
        "origin IN ('declared','inferred','manual')",
    ),
    ("context.synonym", "origin", "'pack'", "origin IN ('manual','agent_suggested')"),
    (
        "context.example_query",
        "origin",
        "'pack'",
        "origin IN ('manual','agent_suggested','feedback')",
    ),
    ("agent.agent_memory", "source_kind", "'pack'", "source_kind IN ('hitl','run','manual')"),
    ("eval.eval_set", "subject_type", "'pack'", "subject_type IN ('context','skill','agent')"),
    (
        "eval.eval_item",
        "origin",
        "'pack'",
        "origin IN ('manual','feedback','real_run','hitl_rule','generated','error_book')",
    ),
    (
        "eval.error_book_entry",
        "fix_kind",
        "'pack_version'",
        "fix_kind IS NULL OR fix_kind IN ('metric_version','synonym','example','knowledge_page',"
        "'source_data','model_config','prompt','code','none')",
    ),
    (
        "inbox.proposal",
        "kind",
        "'pack_activate'",
        "kind IN ('metric_change','synonym','relationship','example','glossary','schema_change',"
        "'skill_publish','source_authority','writeback','document_validity','feedback_fix',"
        "'knowledge_page','data_product')",
    ),
    ("skill.skill", "origin", "'pack'", "origin IN ('manual','from_session','agent_suggested')"),
    ("ingest.quality_rule", "origin", "'pack'", "origin IN ('manual','agent_suggested')"),
    (
        "acquire.reference_entity",
        "registry_source",
        "'pack'",
        "registry_source IN ('sbv_registry','swift_directory','tax_registry','manual')",
    ),
)

COLUMNS = (
    ("org.organization", "industry_code"),
    ("org.data_space", "pack_install_id"),
    ("context.semantic_entity", "canonical_entity_id"),
    ("context.metric", "pack_metric_id"),
    ("context.metric_version", "override_id"),
    ("context.data_product", "pack_version_id"),
    ("eval.error_book_entry", "pack_contribution"),
    ("eval.error_book_entry", "pack_proposal_ref"),
)

COMMENTS = """
COMMENT ON COLUMN governance.behavior_policy.origin IS 'manual, hitl';
COMMENT ON COLUMN context.relationship.origin IS 'declared, inferred, manual';
COMMENT ON COLUMN context.synonym.origin IS 'manual, agent_suggested';
COMMENT ON COLUMN context.example_query.origin IS 'manual, agent_suggested, feedback';
COMMENT ON COLUMN agent.agent_def.template_ref IS 'Agent mẫu mà agent được tạo từ đó';
COMMENT ON COLUMN agent.agent_memory.source_kind IS 'hitl, run, manual';
COMMENT ON TABLE eval.eval_set IS 'Bộ đánh giá dùng chung cho lớp ngữ cảnh, kỹ năng và agent. Use case: UC-07-03, UC-07-06, UC-13-04.';
COMMENT ON COLUMN eval.eval_set.data_space_id IS 'Null với bộ dùng chung cho cả tổ chức';
COMMENT ON COLUMN eval.eval_set.subject_type IS 'context, skill, agent';
COMMENT ON COLUMN eval.eval_set.subject_id IS 'Kỹ năng hoặc agent; null với lớp ngữ cảnh của Data Space';
COMMENT ON COLUMN eval.eval_item.origin IS 'manual, feedback, real_run, hitl_rule, generated, error_book (ca hồi quy từ Error Book, origin_ref = eval.error_book_entry.id, UC-07-14)';
COMMENT ON COLUMN eval.error_book_entry.fix_kind IS 'metric_version, synonym, example, knowledge_page, source_data, model_config, prompt, code, none';
COMMENT ON COLUMN eval.error_book_entry.fix_proposal_id IS 'logic → inbox.proposal; đề xuất sửa chỉ số, đồng nghĩa, trang tri thức';
COMMENT ON TABLE inbox.proposal IS 'Đề xuất thay đổi chờ duyệt: lớp ngữ cảnh, schema, kỹ năng, ghi ngược, hiệu lực văn bản. Use case: UC-01-05, UC-03-05, UC-04-03, UC-05-05, UC-06-04, UC-07-01, UC-07-02, UC-07-03, UC-07-05, UC-07-06.';
COMMENT ON COLUMN inbox.proposal.kind IS 'metric_change, synonym, relationship, example, glossary, schema_change, skill_publish, source_authority, writeback, document_validity, feedback_fix, data_product (kích hoạt hoặc đổi phiên bản data product, UC-07-13), knowledge_page';
COMMENT ON COLUMN skill.skill.origin IS 'manual, from_session, agent_suggested';
COMMENT ON COLUMN ingest.quality_rule.origin IS 'manual, agent_suggested';
COMMENT ON COLUMN acquire.reference_entity.registry_source IS 'sbv_registry, swift_directory, tax_registry, manual';
"""

# The roles are cluster-wide: what they own or were granted in this database goes first. A
# deploy that still lists svc_pack would only recreate an empty login role.
ROLES = """
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['svc_pack', 'pack_read'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('DROP OWNED BY %I', r);
      EXECUTE format('DROP ROLE %I', r);
    END IF;
  END LOOP;
END $$;
"""


def upgrade() -> None:
    sql = ["SET LOCAL ROLE da_owner;", "DROP SCHEMA IF EXISTS pack CASCADE;"]
    sql += [f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column};" for table, column in COLUMNS]
    for table, column, pattern, check in CHECKS:
        name = f"{table.split('.')[1]}_{column}_check"
        sql.append(
            REPLACE_CHECK.format(
                table=table, pattern=pattern.replace("'", "''"), name=name, check=check
            )
        )
    sql.append(COMMENTS)
    sql.append("DELETE FROM agent.tool_def WHERE code LIKE 'pack.%';")
    sql.append(
        "UPDATE org.role SET capabilities = array_remove(capabilities, 'pack_author')"
        " WHERE 'pack_author' = ANY (capabilities);"
    )
    sql.append("RESET ROLE;")
    sql.append(ROLES)
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text("\n".join(sql).replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0017 is not reversed: the pack schema and its data are gone")
