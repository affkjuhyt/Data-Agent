"""Render the committed schema artifacts of the contract.

    python -m libs.contracts.schema_export          # rewrite generated/
    python -m libs.contracts.schema_export --check  # exit 1 if generated/ is stale

`generated/contracts.openapi.json` is what the frontend generates its V2 types
from until indicator-api (NOP-162) serves the same models; `generated/events/`
holds one JSON Schema per (topic, payload schema_version) plus the envelope.
Run it with pydantic 2.9.2 (the kb-backend image): another pydantic minor may
render the same models slightly differently and make --check fail spuriously.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from libs.contracts import CONTRACT_VERSION
from libs.contracts.v1 import (
    OPEN_ENUMS,
    PARTITION_KEY_FIELD,
    PAYLOAD_MODELS,
    PUBLIC_MODELS,
    EventEnvelope,
)

PACKAGE_DIR = Path(__file__).resolve().parent
GENERATED_DIR = PACKAGE_DIR / "generated"
EXAMPLES_DIR = PACKAGE_DIR / "examples" / "v1"
OPENAPI_NAME = "contracts.openapi.json"


def load_example(model: type[BaseModel]) -> Any:
    return json.loads((EXAMPLES_DIR / f"{model.__name__}.json").read_text(encoding="utf-8"))


def _mark_open_enums(definitions: dict[str, Any]) -> None:
    # The compatibility gate (libs.contracts.compat) compares two schema files
    # without importing either side's code, so which enums may grow without a
    # bump has to travel in the schema, like x-schema-version.
    for enum in OPEN_ENUMS:
        if enum.__name__ in definitions:
            definitions[enum.__name__]["x-open-enum"] = True


def _dump(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_openapi() -> str:
    _, top = models_json_schema(
        [(model, "validation") for model in PUBLIC_MODELS],
        ref_template="#/components/schemas/{model}",
    )
    schemas: dict[str, Any] = top["$defs"]
    _mark_open_enums(schemas)
    for model in PUBLIC_MODELS:
        schemas[model.__name__]["examples"] = [load_example(model)]
    return _dump(
        {
            "openapi": "3.1.0",
            "info": {
                "title": "Data Agent V2 contract",
                "version": CONTRACT_VERSION,
                "description": "Shared wire shapes (libs/contracts, NOP-150). No paths: "
                "indicator-api (NOP-162) serves these models under its own routes.",
            },
            "paths": {},
            "components": {"schemas": schemas},
        }
    )


def _event_schema(model: type[BaseModel], title: str, partition_key: str | None = None) -> str:
    schema = model.model_json_schema(mode="validation")
    schema["title"] = title
    _mark_open_enums(schema.get("$defs", {}))
    if model is not EventEnvelope:
        schema["examples"] = [load_example(model)]
    if partition_key is not None:
        # Changing a topic's key reorders its events (COMPATIBILITY.md), so the
        # key is part of the published payload contract.
        schema["x-partition-key"] = partition_key
    return _dump(schema)


def render_all() -> dict[Path, str]:
    files = {GENERATED_DIR / OPENAPI_NAME: render_openapi()}
    files[GENERATED_DIR / "events" / "envelope.v1.schema.json"] = _event_schema(
        EventEnvelope, "EventEnvelope v1"
    )
    for (event_type, version), model in PAYLOAD_MODELS.items():
        name = f"{event_type.value}.v{version}.schema.json"
        files[GENERATED_DIR / "events" / name] = _event_schema(
            model, f"{event_type.value} payload v{version}", PARTITION_KEY_FIELD[event_type]
        )
    return files


def stale_files(files: dict[Path, str]) -> list[Path]:
    stale = [
        p for p, text in files.items() if not p.exists() or p.read_text(encoding="utf-8") != text
    ]
    # A schema file left behind after its payload version was removed is stale too.
    known = set(files)
    stale += sorted(p for p in (GENERATED_DIR / "events").glob("*.json") if p not in known)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if generated/ is out of date")
    args = parser.parse_args(argv)
    files = render_all()
    if args.check:
        stale = stale_files(files)
        for path in stale:
            print(f"stale: {path.relative_to(PACKAGE_DIR)}", file=sys.stderr)
        if stale:
            print(
                "run `python -m libs.contracts.schema_export` and commit generated/",
                file=sys.stderr,
            )
        return 1 if stale else 0
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
