"""Build the screen catalog from ROUTE_MAP + apis.yaml + AccessDenied wiring."""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
ROUTES_TS = REPO / "frontend" / "src" / "routes.tsx"
CONSTANTS_TS = REPO / "frontend" / "src" / "constants.ts"
APIS_YAML = pathlib.Path(__file__).with_name("apis.yaml")
FRONTEND_SRC = REPO / "frontend" / "src"

_LAZY_IMPORT = re.compile(
    r"const\s+(\w+)\s*=\s*React\.lazy\(\s*\(\)\s*=>\s*import\('(\./features/[^']+)'\),?\s*\)"
)
_REQUIRES = re.compile(
    r"\{\s*id:\s*'([A-Z][A-Z0-9_]*)'\s*,\s*label:\s*'[^']*'\s*,\s*requires:\s*'([^']+)'"
)


@dataclass(frozen=True)
class Screen:
    area: str
    page: str
    url: str
    needs_id: bool
    rail_requires: str | None
    component: str
    primary_gets: tuple[str, ...]
    id_from: str | None
    seed_id: str | None
    wires_access_denied: bool

    @property
    def key(self) -> str:
        return f"{self.area}/{self.page}"


def route_map_keys(routes_text: str | None = None) -> dict[str, list[str]]:
    """area → page keys as declared in ROUTE_MAP (page '_' for SINGLE_SCREEN)."""
    text = routes_text if routes_text is not None else ROUTES_TS.read_text()
    start = text.index("export const ROUTE_MAP")
    body = text[start:]
    # Cut at the closing `};` of ROUTE_MAP (first top-level close after the opening brace).
    brace = body.index("{")
    depth = 0
    end = None
    for i, ch in enumerate(body[brace:], start=brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        raise ValueError("ROUTE_MAP brace not closed")
    map_body = body[brace : end + 1]

    areas: dict[str, list[str]] = {}
    current: str | None = None
    # Walk area headers then page entries inside the map body.
    for match in re.finditer(
        r"(?:^\s*(?:'([^']+)'|([a-z][\w-]*))\s*:\s*\{)|"
        r"(?:\[SINGLE_SCREEN\]|([A-Z][A-Z0-9_]*))\s*:\s*\{\s*render:",
        map_body,
        re.MULTILINE,
    ):
        area_q, area_bare, page = match.group(1), match.group(2), match.group(3)
        if area_q or area_bare:
            current = area_q or area_bare
            areas.setdefault(current, [])
            continue
        if current is None:
            continue
        areas[current].append("_" if match.group(0).startswith("[SINGLE_SCREEN]") else page)
    return areas


def lazy_imports(routes_text: str | None = None) -> dict[str, str]:
    """ComponentName → relative import path under frontend/src (without extension)."""
    text = routes_text if routes_text is not None else ROUTES_TS.read_text()
    return {name: path for name, path in _LAZY_IMPORT.findall(text)}


def page_components(routes_text: str | None = None) -> dict[str, str]:
    """`area/page` → component class name used in ROUTE_MAP."""
    text = routes_text if routes_text is not None else ROUTES_TS.read_text()
    start = text.index("export const ROUTE_MAP")
    body = text[start:]
    brace = body.index("{")
    depth = 0
    end = None
    for i, ch in enumerate(body[brace:], start=brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None
    map_body = body[brace : end + 1]

    out: dict[str, str] = {}
    current: str | None = None
    for match in re.finditer(
        r"(?:^\s*(?:'([^']+)'|([a-z][\w-]*))\s*:\s*\{)|"
        r"(?:\[SINGLE_SCREEN\]|([A-Z][A-Z0-9_]*))\s*:\s*\{\s*render:\s*\(\)\s*=>\s*<(\w+)",
        map_body,
        re.MULTILINE,
    ):
        area_q, area_bare, page, component = match.group(1), match.group(2), match.group(3), match.group(4)
        if area_q or area_bare:
            current = area_q or area_bare
            continue
        if current is None or component is None:
            continue
        page_key = "_" if match.group(0).lstrip().startswith("[SINGLE_SCREEN]") else page
        out[f"{current}/{page_key}"] = component
    return out


def rail_requires(constants_text: str | None = None) -> dict[str, str]:
    """page id → capability required by the rail item."""
    text = constants_text if constants_text is not None else CONSTANTS_TS.read_text()
    return {page: cap for page, cap in _REQUIRES.findall(text)}


def component_path(import_rel: str) -> pathlib.Path:
    """Resolve `./features/...` from routes.tsx to a .tsx file."""
    base = FRONTEND_SRC / import_rel[2:]  # strip ./
    for suffix in (".tsx", ".ts", "/index.tsx", "/index.ts"):
        candidate = pathlib.Path(str(base) + suffix) if not suffix.startswith("/") else base / suffix[1:]
        if candidate.is_file():
            return candidate
    # Default to .tsx for error messages.
    return pathlib.Path(str(base) + ".tsx")


def wires_access_denied(component_file: pathlib.Path) -> bool:
    if not component_file.is_file():
        return False
    text = component_file.read_text()
    # AsyncRegion routes a 403 access block to AccessDenied centrally.
    return "AccessDenied" in text or "forbiddenOf" in text or "AsyncRegion" in text


def load_api_overlay(path: pathlib.Path | None = None) -> dict[str, dict]:
    data = yaml.safe_load((path or APIS_YAML).read_text()) or {}
    screens = data.get("screens") or {}
    if not isinstance(screens, dict):
        raise ValueError("apis.yaml screens must be a mapping of area/page → config")
    return screens


def screen_url(area: str, page: str, needs_id: bool) -> str:
    if page == "_":
        return f"/{area}"
    path = f"/{area}/{page.lower()}"
    return f"{path}/{{id}}" if needs_id else path


def build_catalog(
    *,
    routes_text: str | None = None,
    constants_text: str | None = None,
    api_overlay: dict[str, dict] | None = None,
) -> list[Screen]:
    keys = route_map_keys(routes_text)
    components = page_components(routes_text)
    imports = lazy_imports(routes_text)
    requires = rail_requires(constants_text)
    overlay = api_overlay if api_overlay is not None else load_api_overlay()

    screens: list[Screen] = []
    for area, pages in keys.items():
        for page in pages:
            key = f"{area}/{page}"
            conf = overlay.get(key) or {}
            primary = tuple(conf.get("primary_gets") or ())
            needs_id = bool(conf.get("needs_id", any("{id}" in g or "{key}" in g for g in primary)))
            id_from = conf.get("id_from")
            seed_id = conf.get("seed_id")
            component_name = components.get(key)
            if not component_name:
                raise ValueError(f"no component for {key}")
            import_rel = imports.get(component_name)
            if not import_rel:
                raise ValueError(f"no lazy import for {component_name} ({key})")
            path = component_path(import_rel)
            rail_cap = conf.get("rail_requires")
            if rail_cap is None:
                rail_cap = requires.get(page)
            screens.append(
                Screen(
                    area=area,
                    page=page,
                    url=screen_url(area, page, needs_id),
                    needs_id=needs_id,
                    rail_requires=rail_cap,
                    component=str(path.relative_to(REPO)),
                    primary_gets=primary,
                    id_from=id_from,
                    seed_id=str(seed_id) if seed_id else None,
                    wires_access_denied=wires_access_denied(path),
                )
            )
    return screens
