"""tools/licenses.py (PL-15): a forbidden licence fails the check, a clean alternative does not."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

pytestmark = [pytest.mark.unit]

ROOT = pathlib.Path(__file__).parents[3]
_spec = importlib.util.spec_from_file_location("licenses_tool", ROOT / "tools" / "licenses.py")
assert _spec is not None and _spec.loader is not None
lic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lic
_spec.loader.exec_module(lic)
POLICY = json.loads((ROOT / "tools" / "licenses.json").read_text(encoding="utf-8"))


def _pkg(licence: str, name: str = "x", ecosystem: str = "pypi") -> dict[str, str]:
    return {
        "name": name,
        "version": "1",
        "license": licence,
        "component": "api",
        "ecosystem": ecosystem,
    }


@pytest.mark.parametrize(
    ("licence", "kind"),
    [
        ("AGPL-3.0-only", "forbidden"),
        ("GNU Affero General Public License v3", "forbidden"),
        ("SSPL-1.0", "forbidden"),
        ("Elastic License 2.0", "forbidden"),
        ("BUSL-1.1", "forbidden"),
        ("CC-BY-NC-4.0", "forbidden"),
        ("MIT OR AGPL-3.0", "allowed"),  # the product takes MIT
        ("MIT AND AGPL-3.0", "forbidden"),  # both parts are carried
        ("GPL-3.0-or-later", "review"),
        ("LGPL-2.1-or-later", "allowed"),
        ("MPL-2.0", "allowed"),
        ("Apache-2.0 AND MIT", "allowed"),
        ("", "unknown"),
    ],
)
def test_each_licence_is_classified(licence: str, kind: str) -> None:
    assert lic.classify(_pkg(licence), POLICY) == kind


def test_a_vetted_exception_is_allowed() -> None:
    policy = {**POLICY, "exceptions": {"pypi:odd": "checked by hand"}}
    assert lic.classify(_pkg("", name="Odd"), policy) == "allowed"


def test_the_report_fails_on_a_forbidden_package(tmp_path: pathlib.Path) -> None:
    scan = tmp_path / "api.json"
    scan.write_text(json.dumps([_pkg("MIT", "fine"), _pkg("AGPL-3.0", "bad")]))
    notices = tmp_path / "NOTICES.md"
    assert lic.main(["report", str(scan), "--notices", str(notices)]) == 1
    text = notices.read_text()
    assert "| bad | 1 | AGPL-3.0 | api | pypi |" in text and "1 forbidden" in text
    scan.write_text(json.dumps([_pkg("MIT", "fine")]))
    assert lic.main(["report", str(scan)]) == 0


def test_the_frontend_ships_only_its_non_dev_packages(tmp_path: pathlib.Path) -> None:
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "packages": {
                    "": {"name": "root"},
                    "frontend": {"name": "frontend", "version": "0.0.0"},
                    "node_modules/react": {"version": "18.3.1", "license": "MIT"},
                    "node_modules/vitest": {"version": "2.0.0", "license": "MIT", "dev": True},
                    "node_modules/frontend": {"link": True, "resolved": "frontend"},
                }
            }
        )
    )
    assert [(p["name"], p["license"]) for p in lic.npm(lock, "frontend")] == [("react", "MIT")]


def test_the_declared_licence_comes_first() -> None:
    class Meta(dict):  # PackageMetadata's get / get_all
        def get_all(self, key: str) -> list[str] | None:
            v = self.get(key)
            return v if isinstance(v, list) else None

    assert lic.licence_of(Meta({"License-Expression": "MIT", "License": "BSD"})) == "MIT"
    classifiers = [
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python",
    ]
    assert lic.licence_of(Meta({"Classifier": classifiers})) == "Apache Software License"
    assert lic.licence_of(Meta({"License": "x" * 200})) == ""  # the whole text is not a name
