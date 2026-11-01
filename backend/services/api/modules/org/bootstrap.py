"""The organisation a fresh install starts with (GEN-07): its row, the default roles, the first admin.

Before this, the organisation row and every role came only from the demo seed, so an install
without it resolved nobody (identity.py needs an active organisation) and could seat nobody (a
Data Space member needs a role). Run where the api runs, with its svc_org credentials; the
deploy (deploy/ansible/roles/k8s_org) calls it after creating the admin in Keycloak:

    python -m services.api.modules.org.bootstrap --org <id> --code <code> --name <name> \
        --locale vi-VN --time-zone Asia/Ho_Chi_Minh --currency VND \
        --admin-username <username> --admin-email <email> --admin-external-id <keycloak id>

Every value is required: nothing is guessed for a customer. Re-running it is safe. The
organisation's name, locale, time zone and currency follow the arguments, its code never changes; a default role that
already exists is left as it is, since an administrator may have changed it; the admin keeps the
system-admin role. An admin username already bound to another Keycloak user is refused rather than
repointed.

The default roles are the design's actors (tab 3b, org.role.code "AC01 đến AC11"), as approved on
01/11/2026: AC07 (the industry-pack author) went with the packs and AC11 is not used, AC01 is the
ask-only role an agent is seated with (public.open_agent_account), AC08 to AC10 fill roles the
design names no code for. The first admin holds AC05 only: compliance (AC06) is the second approver of what
an admin enables (a frontier model provider, for one), so it has to be someone else.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
import zoneinfo
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import org
from services.api.kernel.auth import CAPABILITIES

ROLE = "svc_org"
ADMIN_ROLE = "AC05"


@dataclass(frozen=True)
class DefaultRole:
    code: str
    name: str
    capabilities: tuple[str, ...]


DEFAULT_ROLES: tuple[DefaultRole, ...] = (
    DefaultRole("AC01", "Người dùng nghiệp vụ", ("ask",)),
    DefaultRole("AC02", "Lãnh đạo", ("ask", "exec_vault")),
    DefaultRole("AC03", "Người phụ trách dữ liệu", ("ask", "view_detail", "steward", "approve")),
    DefaultRole(
        "AC04",
        "Kỹ sư dữ liệu",
        ("ask", "view_detail", "upload", "prepare_data", "admin_data", "writeback"),
    ),
    DefaultRole("AC05", "Quản trị hệ thống", ("admin",)),
    DefaultRole("AC06", "Tuân thủ và pháp chế", ("compliance", "audit_read")),
    DefaultRole(
        "AC08",
        "Chuyên viên phân tích",
        ("ask", "view_detail", "upload", "prepare_data", "run_agent", "create_skill"),
    ),
    DefaultRole("AC09", "Người dựng agent", ("ask", "run_agent", "create_agent", "create_skill")),
    DefaultRole("AC10", "Kiểm toán nội bộ", ("audit_read",)),
)

_CODE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_LOCALE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class BootstrapRefused(ValueError):
    """The arguments or the database say this run must not write anything."""


@dataclass(frozen=True)
class Organisation:
    id: uuid.UUID
    code: str
    name: str
    locale: str
    time_zone: str
    currency: str
    admin_username: str
    admin_email: str
    admin_external_id: str

    def check(self) -> None:
        problems = []
        if not _CODE.fullmatch(self.code):
            problems.append(f"code {self.code!r}: lowercase letters, digits and '-'")
        if not self.name.strip():
            problems.append("name is empty")
        if not _LOCALE.fullmatch(self.locale):
            problems.append(f"locale {self.locale!r}: a language tag such as vi-VN")
        try:
            zoneinfo.ZoneInfo(self.time_zone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            problems.append(f"time zone {self.time_zone!r}: an IANA name such as Asia/Ho_Chi_Minh")
        if not _CURRENCY.fullmatch(self.currency):
            problems.append(f"currency {self.currency!r}: an ISO 4217 code such as VND")
        if not self.admin_username.strip() or not self.admin_external_id.strip():
            problems.append("the admin needs a username and its Keycloak id")
        if "@" not in self.admin_email:
            problems.append(f"admin email {self.admin_email!r}")
        if problems:
            raise BootstrapRefused("; ".join(problems))


async def bootstrap(o: Organisation) -> dict[str, list[str]]:
    """Write the organisation, its missing default roles and its admin; what was done, by kind."""
    o.check()
    unknown = {c for r in DEFAULT_ROLES for c in r.capabilities} - CAPABILITIES
    assert not unknown, f"default roles name unknown capabilities: {sorted(unknown)}"
    report: dict[str, list[str]] = {"created": [], "kept": [], "updated": []}
    async with scoped_session(ROLE, o.id) as session:
        existing = await session.get(org.Organization, o.id)
        if existing is None:
            session.add(
                org.Organization(
                    id=o.id,
                    code=o.code,
                    name=o.name,
                    locale=o.locale,
                    time_zone=o.time_zone,
                    settings={"currency": o.currency},
                )
            )
            report["created"].append(f"organisation {o.code}")
        else:
            if existing.code != o.code:
                # Same id, so the same organisation: its code is what other systems may already
                # know it by, so a differing argument is reported, never applied.
                report["kept"].append(f"code {existing.code} (asked {o.code})")
            existing.name, existing.locale, existing.time_zone = o.name, o.locale, o.time_zone
            # The currency has no column (tab 3b): it lives in settings, beside the thresholds.
            existing.settings = {**(existing.settings or {}), "currency": o.currency}
            report["updated"].append(f"organisation {o.code}")
        await session.flush()

        for role in DEFAULT_ROLES:
            inserted = await session.scalar(
                insert(org.Role)
                .values(
                    id=uuid7(),
                    org_id=o.id,
                    code=role.code,
                    name=role.name,
                    capabilities=list(role.capabilities),
                    is_system=True,
                )
                .on_conflict_do_nothing(index_elements=["org_id", "code"])
                .returning(org.Role.id)
            )
            report["created" if inserted else "kept"].append(f"role {role.code}")

        user = await session.scalar(
            select(org.AppUser).where(org.AppUser.username == o.admin_username)
        )
        if user is None:
            user = org.AppUser(
                id=uuid7(),
                org_id=o.id,
                kind="human",
                external_id=o.admin_external_id,
                username=o.admin_username,
                display_name=o.admin_username,
                email=o.admin_email,
                status="active",
            )
            session.add(user)
            await session.flush()
            report["created"].append(f"admin {o.admin_username}")
        elif user.external_id not in (None, o.admin_external_id):
            raise BootstrapRefused(
                f"user {o.admin_username!r} is bound to another Keycloak user: refusing to repoint it"
            )
        else:
            user.external_id, user.email, user.status = o.admin_external_id, o.admin_email, "active"
            report["kept"].append(f"admin {o.admin_username}")

        admin_role = await session.scalar(select(org.Role.id).where(org.Role.code == ADMIN_ROLE))
        granted = await session.scalar(
            insert(org.UserRole)
            .values(id=uuid7(), org_id=o.id, user_id=user.id, role_id=admin_role, source="manual")
            .on_conflict_do_nothing(index_elements=["user_id", "role_id", "source"])
            .returning(org.UserRole.id)
        )
        report["created" if granted else "kept"].append(f"{ADMIN_ROLE} for {o.admin_username}")
    return report


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="bootstrap", description=__doc__.splitlines()[0])
    parser.add_argument("--org", required=True, type=uuid.UUID, help="the organisation's id")
    for name in ("code", "name", "locale", "time-zone", "currency"):
        parser.add_argument(f"--{name}", required=True)
    for name in ("admin-username", "admin-email", "admin-external-id"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    o = Organisation(
        args.org,
        args.code,
        args.name,
        args.locale,
        args.time_zone,
        args.currency,
        args.admin_username,
        args.admin_email,
        args.admin_external_id,
    )
    try:
        report = asyncio.run(bootstrap(o))
    except BootstrapRefused as exc:
        sys.stderr.write(f"org bootstrap refused: {exc}\n")
        return 1
    for kind, items in report.items():
        for item in items:
            sys.stdout.write(f"{kind}: {item}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
