"""One decision for every two-person step: may this caller approve their own work?

The organisation's switch (org.organization.allow_self_approval) rides on the Principal. A
call site names the people the caller must not be (the author, the requester, ...) and gets
back whether the caller is one of them under a permissive org. Writing the audit row for that
case is audit.public.record_self_approval: the kernel may not import a module.
"""

from __future__ import annotations

import uuid

from services.api.kernel.auth import Principal
from services.api.kernel.problems import Problem


def two_people(
    principal: Principal,
    *others: uuid.UUID | None,
    message: str,
    status: int = 403,
    code: str = "forbidden",
) -> bool:
    """True when the caller is one of `others` and the org allows it (a self-approval);
    False when the caller is none of them; raises Problem(status, code, message) otherwise.
    None in `others` is ignored."""
    if principal.user_id not in others:
        return False
    if principal.self_approval:
        return True
    raise Problem(status, code, message)
