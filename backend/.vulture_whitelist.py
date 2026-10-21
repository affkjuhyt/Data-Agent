# Vulture whitelist — names that are USED dynamically (so static analysis can't
# see the reference) and must NOT be reported as dead code.
#
# Structural noise (route handlers, validators, REGISTRY classes, Pydantic/ORM
# field modules) is handled by [tool.vulture] in pyproject.toml. This file is
# for the individual names that are left: each entry is a deliberate "this is
# alive" assertion with its reason, reviewed like code. Vulture only needs to
# see the name used, so an entry is a bare attribute access.
#
# Usage: `make deadcode` (vulture reads this via [tool.vulture].paths).
# ruff: noqa
# mypy: ignore-errors

_ = object()

# Pydantic reads `model_config` off the class; nothing references it by name.
_.model_config

# Attribute writes on third-party objects (psycopg2 connection, openpyxl cells
# and sheets): the library reads them, our code only sets them.
_.autocommit
_.fill
_.font
_.freeze_panes

# libs/shared/audit.py AuditEvent fields: serialised with dataclasses.asdict()
# into the audit record, never read attribute by attribute.
_.statement_redacted
_.statement_sha256
_.event_id
_.occurred_at

# Test hook: tests/unit/workflow_runtime/test_platform_routers.py clears the LiteLLM health cache
# between cases (kept on purpose in the 2026-09 dead-code sweep).
_.reset_health_cache
