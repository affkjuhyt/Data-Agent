"""Optional dependency stubs for platform-worker unit tests.

These tests exercise scheduling/ORM behaviour and patch the external clients
they need. The lightweight stubs keep imports cheap when object-store/vector
drivers are not installed in the unit-test environment.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _install_missing_module(name: str, attrs: dict[str, object]) -> None:
    try:
        __import__(name)
    except ImportError:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module


_install_missing_module("minio", {"Minio": MagicMock})
_install_missing_module("neo4j", {"GraphDatabase": MagicMock})
_install_missing_module("pymongo", {"MongoClient": MagicMock})
_install_missing_module("qdrant_client", {"QdrantClient": MagicMock})

try:
    __import__("qdrant_client.http.models")
except ImportError:
    http = types.ModuleType("qdrant_client.http")
    models = types.ModuleType("qdrant_client.http.models")
    models.Filter = MagicMock
    models.FieldCondition = MagicMock
    models.MatchValue = MagicMock
    models.FilterSelector = MagicMock
    sys.modules.setdefault("qdrant_client.http", http)
    sys.modules["qdrant_client.http.models"] = models

_install_missing_module("kafka", {"KafkaProducer": MagicMock})
try:
    __import__("kafka.errors")
except ImportError:
    errors = types.ModuleType("kafka.errors")
    errors.NoBrokersAvailable = type("NoBrokersAvailable", (Exception,), {})
    sys.modules["kafka.errors"] = errors
