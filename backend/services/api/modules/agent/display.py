"""Words the agent screens draw from rows they already store.

The list endpoints used to return the tables and leave the sentence to the client. A goal, a
trigger and a permission are one row each, so the list says them here and every screen reads the
same sentence.
"""

from __future__ import annotations

_PERMISSION = (
    ("read", "Đọc"),
    ("write_derived", "Đề xuất"),
    ("send_external", "Gửi ra ngoài"),
)
_KIND = {
    "schedule": "Theo lịch",
    "event": "Khi có sự kiện",
    "threshold": "Khi vượt ngưỡng",
    "agent_call": "Khi agent khác gọi",
    "manual": "Khi được giao",
}
_DOW = {
    "0": "Chủ nhật",
    "1": "thứ Hai",
    "2": "thứ Ba",
    "3": "thứ Tư",
    "4": "thứ Năm",
    "5": "thứ Sáu",
    "6": "thứ Bảy",
    "7": "Chủ nhật",
}


def permission_text(levels: list[str]) -> str | None:
    """ "Đọc · Đề xuất" from the permission levels of the current config, in a fixed order."""
    have = set(levels)
    parts = [label for level, label in _PERMISSION if level in have]
    return " · ".join(parts) or None


def _clock(cron: str) -> str | None:
    parts = cron.split()
    if len(parts) != 5 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    clock = f"{int(parts[1]):02d}:{int(parts[0]):02d}"
    day = _DOW.get(parts[4])
    return f"{clock} {day}" if day else clock


def trigger_phrase(kind: str, cron: str | None, event_type: str | None) -> str:
    """One trigger in the words the team table uses."""
    lead = _KIND.get(kind, kind)
    if kind == "schedule" and cron:
        clock = _clock(cron)
        return f"{lead} {clock}" if clock else lead
    if kind == "event" and event_type:
        return f"{lead} {event_type}"
    return lead


def trigger_text(triggers: list[tuple[str, str | None, str | None]]) -> str | None:
    phrases = [trigger_phrase(kind, cron, event) for kind, cron, event in triggers]
    return " · ".join(phrases) or None


def runs_window(total: int, needs_person: int, errors: int) -> dict[str, int] | None:
    """None when the agent did not run, so the cell stays empty rather than "0 lượt"."""
    if total <= 0:
        return None
    return {
        "count": total,
        "needs_approval_pct": round(100 * needs_person / total),
        "errors": errors,
    }
