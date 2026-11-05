"""Screen audit: probe every ROUTE_MAP screen's primary GETs as engineer and viewer.

Writes tests/screen_audit/out/screen_audit.tsv. Does not fail on empty / no_seed_id /
access_as_load_error — those labels are the deliverable. Fails only when the harness
itself cannot run (login, catalog).
"""

from __future__ import annotations

import os
import pathlib
from collections import Counter

import httpx
import pytest

from tests.conftest import live_token
from tests.screen_audit.catalog import build_catalog
from tests.screen_audit.probe import probe_screen, resolve_id

pytestmark = pytest.mark.e2e

DATA_SPACE = os.environ.get("E2E_DATA_SPACE", "DS-AW")
OUT = pathlib.Path(__file__).resolve().parents[1] / "screen_audit" / "out" / "screen_audit.tsv"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Data-Space": DATA_SPACE}


@pytest.fixture(scope="module")
def engineer_token(live_stack: dict[str, str]) -> str:
    return live_token(live_stack["keycloak"], "engineer")


@pytest.fixture(scope="module")
def viewer_token(live_stack: dict[str, str]) -> str:
    return live_token(live_stack["keycloak"], "viewer")


@pytest.fixture(scope="module")
def kong(live_stack: dict[str, str]) -> str:
    return live_stack["kong"]


def test_screen_audit_probe_writes_report(
    kong: str, engineer_token: str, viewer_token: str
) -> None:
    screens = build_catalog()
    assert screens, "catalog empty"

    eng_headers = _headers(engineer_token)
    view_headers = _headers(viewer_token)
    rows: list[str] = []
    header = "\t".join(
        [
            "area",
            "page",
            "url",
            "rail_requires",
            "wires_access_denied",
            "engineer_label",
            "engineer_status",
            "viewer_label",
            "viewer_status",
            "detail",
        ]
    )
    rows.append(header)

    counts: Counter[str] = Counter()
    with httpx.Client() as client:
        # Resolve ids once as engineer so both roles probe the same record.
        id_cache: dict[str, str | None] = {}
        for screen in screens:
            cache_key = screen.id_from or screen.key
            if screen.needs_id and cache_key not in id_cache:
                id_cache[cache_key] = resolve_id(client, kong, screen, eng_headers)
            record_id = id_cache.get(cache_key) if screen.needs_id else None

            eng_label, eng_status, eng_detail = probe_screen(
                client, kong, screen, eng_headers, record_id=record_id
            )
            view_label, view_status, view_detail = probe_screen(
                client, kong, screen, view_headers, record_id=record_id
            )
            # Rail-gated screens: annotate when the viewer is refused and the rail
            # would have hidden the entry for them.
            if (
                screen.rail_requires
                and view_label in {"access_as_load_error", "access_denied_ok", "forbidden_plain"}
            ):
                view_label = "hidden_rail"

            counts[f"engineer:{eng_label}"] += 1
            counts[f"viewer:{view_label}"] += 1
            rows.append(
                "\t".join(
                    [
                        screen.area,
                        screen.page,
                        screen.url,
                        screen.rail_requires or "",
                        "1" if screen.wires_access_denied else "0",
                        eng_label,
                        str(eng_status),
                        view_label,
                        str(view_status),
                        eng_detail or view_detail,
                    ]
                )
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(rows) + "\n")

    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"screen_audit wrote {OUT} ({len(screens)} screens); {summary}")

    # Harness health only: engineer must authenticate and hit at least one content row
    # on a seeded Agents surface.
    assert counts["engineer:content"] + counts["engineer:empty"] >= 1
    agents_team = next(r for r in rows[1:] if r.startswith("agents\tTEAM\t"))
    assert "\tcontent\t" in agents_team or "\tempty\t" in agents_team, agents_team
