"""Mock API of Contract v1 (NOP-151), for development and tests only.

FE, Data and Agent branches call it instead of indicator-api until NOP-162
serves the same models. It answers only with libs.contracts.v1 models read from
tests/fixtures/contracts/mock/ and simulates no backend logic; usage is in
README.md next to this file.
"""

from __future__ import annotations

from tests.contract_mock.app import create_app

__all__ = ["create_app"]
