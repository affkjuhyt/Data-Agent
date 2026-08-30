"""Data-access audit (backend/libs/shared/audit.py) — who read what.

The gap this closes: the audit pipeline covered logins only, so after an
incident there was no answer to "who read this data, when, and what came back".

Two properties carry the weight, and both fail silently if they regress:

  * **redaction** — a WHERE clause routinely contains the personal data the
    audit exists to protect, so storing raw SQL would make this table the
    largest unmasked copy of it in the platform;
  * **never failing the turn** — a broken audit sink is an operational problem,
    not a reason to refuse a user's question.
"""
from __future__ import annotations

import pytest

from libs.shared import audit

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_sink():
    """Every test installs its own sink; make sure none leaks between them."""
    audit.set_sink(None)
    yield
    audit.set_sink(None)


def _capture():
    sent: list[tuple[str, str, dict]] = []
    audit.set_sink(lambda topic, key, value: sent.append((topic, key, value)))
    return sent


class TestRedaction:
    def test_string_literals_are_removed(self):
        """The single most important case: the value in a WHERE clause IS the
        personal data, and it must not be copied into the audit table."""
        redacted = audit.redact_sql("SELECT * FROM users WHERE email = 'alice@example.com'")

        assert "alice@example.com" not in redacted
        assert "FROM users" in redacted
        assert "email = ?" in redacted

    def test_numeric_literals_are_removed(self):
        redacted = audit.redact_sql("SELECT * FROM salaries WHERE amount > 250000")

        assert "250000" not in redacted
        assert "amount > ?" in redacted

    def test_escaped_quotes_do_not_end_the_literal_early(self):
        """`'O''Brien'` is ONE literal. Stopping at the inner quote would leak
        the tail of the value into the record."""
        redacted = audit.redact_sql("SELECT * FROM t WHERE name = 'O''Brien' AND city = 'Hanoi'")

        assert "Brien" not in redacted
        assert "Hanoi" not in redacted

    def test_in_lists_are_fully_redacted(self):
        redacted = audit.redact_sql(
            "SELECT * FROM users WHERE id IN ('u-1','u-2','u-3')"
        )

        assert "u-1" not in redacted and "u-3" not in redacted

    def test_comments_are_stripped(self):
        """No auditable structure, and a favourite landing spot for an injection
        payload — not something to replay into a log viewer."""
        redacted = audit.redact_sql(
            "SELECT 1 -- ignore previous instructions\nFROM t /* and this */ WHERE x = 1"
        )

        assert "ignore previous instructions" not in redacted
        assert "and this" not in redacted

    def test_dollar_quoted_bodies_are_removed(self):
        redacted = audit.redact_sql("SELECT $tag$secret-value$tag$ FROM t")

        assert "secret-value" not in redacted

    def test_column_and_table_names_survive(self):
        """Redaction has to keep the SHAPE — which tables, which columns, which
        predicates — or the record answers nothing."""
        redacted = audit.redact_sql(
            "SELECT region, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY region"
        )

        for kept in ("region", "SUM(amount)", "orders", "status", "GROUP BY"):
            assert kept in redacted

    def test_identifiers_containing_digits_are_not_mangled(self):
        redacted = audit.redact_sql("SELECT col1, x2y FROM table_2024")

        assert "col1" in redacted
        assert "table_2024" in redacted

    def test_very_long_statement_is_truncated(self):
        redacted = audit.redact_sql("SELECT " + ", ".join(f"col_{i}" for i in range(5000)) + " FROM t")

        assert len(redacted) < audit._MAX_REDACTED_CHARS + 100
        assert "truncated" in redacted

    def test_empty_input_is_safe(self):
        assert audit.redact_sql("") == ""
        assert audit.redact_sql(None) == ""


class TestStatementHash:
    def test_hash_is_of_the_original_not_the_redacted_form(self):
        """Hashing the redacted text would collide every query differing only by
        its arguments — exactly the distinction an investigation needs."""
        a = audit.statement_hash("SELECT * FROM t WHERE id = 1")
        b = audit.statement_hash("SELECT * FROM t WHERE id = 2")

        assert a != b

    def test_identical_statements_correlate(self):
        sql = "SELECT * FROM t WHERE id = 1"

        assert audit.statement_hash(sql) == audit.statement_hash(sql)

    def test_hash_does_not_expose_the_value(self):
        h = audit.statement_hash("SELECT * FROM users WHERE ssn = '123-45-6789'")

        assert "123-45-6789" not in h
        assert len(h) == 64


class TestTableExtraction:
    def test_from_and_join_targets_are_captured(self):
        tables = audit.extract_tables(
            "SELECT * FROM orders o JOIN customers c ON c.id = o.customer_id"
        )

        assert set(tables) == {"orders", "customers"}

    def test_schema_qualified_names_are_kept_whole(self):
        assert audit.extract_tables("SELECT * FROM analytics.fct_sales") == ["analytics.fct_sales"]

    def test_the_sandbox_wrapper_alias_is_not_reported_as_a_table(self):
        """sandbox-executor wraps every statement in `AS _sandboxed_query`;
        that alias is ours, not the tenant's data."""
        tables = audit.extract_tables(
            "SELECT * FROM (\nSELECT * FROM orders\n) AS _sandboxed_query LIMIT 10001"
        )

        assert tables == ["orders"]

    def test_duplicates_are_collapsed_preserving_order(self):
        assert audit.extract_tables("SELECT * FROM a JOIN b ON 1=1 JOIN a ON 1=1") == ["a", "b"]


class TestEmission:
    def test_event_reaches_the_sink_keyed_by_tenant(self):
        """Keyed by tenant so one tenant's trail stays ordered within a
        partition — an investigation reads it in the order it happened."""
        sent = _capture()

        audit.record_sql_access(
            tenant_id="t-1", user_id="u-1", user_roles=["business-operator"],
            target="Sales", sql="SELECT 1 FROM orders", row_count=1,
        )

        topic, key, value = sent[0]
        assert topic == audit.TOPIC
        assert key == "t-1"
        assert value["action"] == audit.ACTION_SQL_QUERY
        assert value["user_roles"] == ["business-operator"]
        assert value["target_objects"] == ["orders"]

    def test_record_sql_access_redacts_and_hashes_for_the_caller(self):
        """So no call site has to remember to."""
        sent = _capture()

        audit.record_sql_access(
            tenant_id="t-1", user_id="u-1",
            sql="SELECT * FROM users WHERE email = 'alice@example.com'",
        )

        value = sent[0][2]
        assert "alice@example.com" not in value["statement_redacted"]
        assert len(value["statement_sha256"]) == 64

    def test_a_broken_sink_does_not_raise(self):
        """The whole contract: auditing records access, it does not gate it."""
        def explode(topic, key, value):
            raise RuntimeError("kafka down")

        audit.set_sink(explode)

        audit.record_sql_access(tenant_id="t-1", user_id="u-1", sql="SELECT 1")

    def test_no_sink_configured_does_not_raise(self):
        audit.set_sink(None)

        audit.record_sql_access(tenant_id="t-1", user_id="u-1", sql="SELECT 1")

    def test_each_event_gets_a_unique_id_for_dedup(self):
        """Kafka is at-least-once; the consumer dedups on this."""
        sent = _capture()

        audit.record_sql_access(tenant_id="t", user_id="u", sql="SELECT 1")
        audit.record_sql_access(tenant_id="t", user_id="u", sql="SELECT 1")

        assert sent[0][2]["event_id"] != sent[1][2]["event_id"]

    def test_missing_user_falls_back_to_anonymous_not_empty(self):
        """An unattributed access still has to be a record — an empty actor
        would violate the consumer's NOT NULL and be dropped."""
        sent = _capture()

        audit.record_sql_access(tenant_id="t-1", user_id="", sql="SELECT 1")

        assert sent[0][2]["user_id"] == "anonymous"

    @pytest.mark.parametrize("decision", [audit.DECISION_ALLOW, audit.DECISION_DENY, audit.DECISION_ERROR])
    def test_every_decision_is_recorded_not_just_success(self, decision):
        """An audit that only logs successes cannot show an attempt."""
        sent = _capture()

        audit.record_sql_access(
            tenant_id="t-1", user_id="u-1", sql="DROP TABLE x", decision=decision,
        )

        assert sent[0][2]["decision"] == decision
