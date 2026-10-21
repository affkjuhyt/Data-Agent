"""The acquire rules the database cannot hold: crawl scope (BR-149), OpenBao references, the legal
review dates (BR-174), the source and run state machines. Pure functions, tested without a database;
what the CHECKs and triggers of routines/acquire.sql already refuse is not repeated here."""

from __future__ import annotations

import calendar
import datetime
import re
import uuid
from collections.abc import Iterable
from urllib.parse import urlsplit

from services.api.kernel.problems import FieldError, Problem

# crawl_source.status (tab 3b D4.22). A retired source never moves again.
PAUSABLE_SOURCE = frozenset({"active", "quarantined"})
RESUMABLE_SOURCE = frozenset({"paused", "quarantined"})
# crawl_run.status: the crawler is still working on these (one at a time per source, unique index).
RUNNING_RUN = frozenset({"queued", "fetching", "extracting"})
# A run whose records may still be reviewed or which may be published.
PUBLISHABLE_RUN = frozenset({"in_review", "quarantined"})
CLOSED_RUN = frozenset({"published", "failed"})
# crawl_schema_version.status that went through an approval (CHECK approved_by on both).
APPROVED_SCHEMA = frozenset({"active", "superseded"})
TESTABLE_SCHEMA = frozenset({"draft", "testing"})

# BR-174: default review period six months, never more than 184 days after the review.
LEGAL_REVIEW_MONTHS = 6
LEGAL_REVIEW_MAX_DAYS = 184
LEGAL_CHECKLIST = ("terms", "robots", "copyright", "personal_data")
# BR-151: a schema version is approved from 95% of required fields extracted right.
MIN_TEST_ACCURACY = 0.95
# UC-14-04 E2: below 90% on the random sample the run is not published; UC-14-02 reopens.
MIN_SAMPLE_ACCURACY = 0.90

_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
# The KV v2 layout every service already reads (auth_api's Secrets Vault, warehouse_secrets.py):
# tenants/<org id>/<key>. The org segment is what keeps one organisation off another's secrets.
_SECRET_KEY = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
_CRON_FIELD = re.compile(r"^[0-9A-Za-z*/,?LW#-]+$")


def invalid(field: str, message: str, code: str = "invalid") -> Problem:
    return Problem(
        422,
        "validation_failed",
        message,
        errors=[FieldError(field=field, code=code, message=message)],
    )


# Names that resolve inside the cluster or a host: never a crawl domain, or the crawler would read
# internal services (the crawler refuses private addresses again at request time).
_INTERNAL_SUFFIXES = (".local", ".internal", ".svc", ".localhost", ".lan", ".home.arpa")


def normalise_domain(domain: str) -> str:
    value = domain.strip().lower().rstrip(".")
    if not _DOMAIN.match(value):
        raise ValueError(f"{domain!r} is not a domain name")
    if value.endswith(_INTERNAL_SUFFIXES):
        raise ValueError(f"{domain!r} is an internal name")
    return value


def in_domains(url: str, domains: Iterable[str]) -> bool:
    """BR-149: the host is one of the declared domains or a subdomain of one."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def check_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{url!r} is not an http(s) URL")
    if parts.username or parts.password:
        # BR-149: no logging in; credentials go through auth_mode + secret_ref only.
        raise ValueError(f"{url!r} carries credentials")
    return url.strip()


def check_urls_in_scope(field: str, urls: Iterable[str], domains: Iterable[str]) -> None:
    allowed = list(domains)
    outside = [u for u in urls if not in_domains(u, allowed)]
    if outside:
        raise invalid(
            field,
            f"{len(outside)} URL(s) are outside allowed_domains (BR-149): {', '.join(outside[:3])}",
            "out_of_scope",
        )


def check_cron(expression: str) -> str:
    fields = expression.split()
    if len(fields) not in (5, 6) or not all(_CRON_FIELD.match(f) for f in fields):
        raise ValueError("schedule_cron is a cron expression of 5 or 6 fields")
    return " ".join(fields)


def check_secret_ref(secret_ref: str, org_id: uuid.UUID) -> str:
    """An OpenBao path under the caller's organisation; the secret itself never reaches the api.

    Only under crawl-sources/: the crawler's AppRole reads nothing else, so a source cannot name
    another secret of the organisation and have it sent to a site as a bearer token."""
    prefix = f"tenants/{org_id}/crawl-sources/"
    if not secret_ref.startswith(prefix) or ".." in secret_ref.split("/"):
        raise invalid(
            "secret_ref",
            f"secret_ref is an OpenBao path under {prefix}<key> of your organisation.",
        )
    if not _SECRET_KEY.match(secret_ref[len(prefix) :]):
        raise invalid("secret_ref", "secret_ref has an invalid key name.")
    return secret_ref


def add_months(moment: datetime.datetime, months: int) -> datetime.datetime:
    """Calendar months, clamped to the month's last day (10/10/2026 + 6 → 10/04/2027)."""
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def legal_review_due(
    reviewed_at: datetime.datetime, requested: datetime.datetime | None
) -> datetime.datetime:
    """BR-174: six months by default; a chosen date lies after the review, within 184 days."""
    due = requested or add_months(reviewed_at, LEGAL_REVIEW_MONTHS)
    if due.tzinfo is None:
        raise invalid("legal_review_due_at", "legal_review_due_at needs a time zone (A1.1).")
    if due <= reviewed_at:
        raise invalid("legal_review_due_at", "legal_review_due_at must be in the future.")
    if due > reviewed_at + datetime.timedelta(days=LEGAL_REVIEW_MAX_DAYS):
        raise invalid(
            "legal_review_due_at",
            f"The legal review is due within {LEGAL_REVIEW_MAX_DAYS} days (BR-174).",
        )
    return due


def source_transition(status: str, allowed: Iterable[str], action: str) -> None:
    if status not in allowed:
        raise Problem(409, "invalid_state", f"A source in status {status} cannot {action}.")


def legal_in_force(
    legal_status: str, due_at: datetime.datetime | None, now: datetime.datetime
) -> bool:
    return legal_status == "approved" and due_at is not None and due_at > now
