"""Every business definition, found by walking domains/ (P0-09 BR-1: one place).

openapi.json and the contract test both read this list, so a model added to any
domain is published and version-checked without being registered by hand.
"""

from __future__ import annotations

import importlib
import pkgutil

import services.indicator_api.domains as domains
from services.indicator_api.domains.contract import ContractModel


def contract_models() -> list[type[ContractModel]]:
    """Public contract models, sorted by name; private bases (`_Name`) are left out."""
    for module in pkgutil.walk_packages(domains.__path__, prefix=f"{domains.__name__}."):
        importlib.import_module(module.name)
    found: set[type[ContractModel]] = set()
    stack = list(ContractModel.__subclasses__())
    while stack:
        model = stack.pop()
        stack.extend(model.__subclasses__())
        # Only models defined in domains/: a subclass declared elsewhere (a test,
        # a router) is not part of the shared definition.
        if model.__module__.startswith(domains.__name__) and not model.__name__.startswith("_"):
            found.add(model)
    return sorted(found, key=lambda m: m.__name__)
