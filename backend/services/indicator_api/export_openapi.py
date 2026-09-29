"""Write, or check, the committed openapi.json (P0-09): the input the screens generate types from.

    python -m services.indicator_api.export_openapi           # rewrite the file
    python -m services.indicator_api.export_openapi --check   # exit 1 if it is stale

No server is started: the schema comes straight from the app object.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from services.indicator_api.main import create_app

OPENAPI_PATH = Path(__file__).with_name("openapi.json")


def render() -> str:
    # Sorted keys and a trailing newline: the file is diffed in CI and in review,
    # so its bytes must depend only on the schema.
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if openapi.json is stale")
    args = parser.parse_args(argv)
    rendered = render()
    if args.check:
        committed = OPENAPI_PATH.read_text(encoding="utf-8") if OPENAPI_PATH.exists() else ""
        if committed != rendered:
            print(
                f"{OPENAPI_PATH} is stale: run `python -m services.indicator_api.export_openapi` "
                "and commit the result",
                file=sys.stderr,
            )
            return 1
        print(f"{OPENAPI_PATH.name} is current")
        return 0
    OPENAPI_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {OPENAPI_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
