"""platform-workers/common — env -> settings, and the Kafka SASL kwargs the
factories hand to kafka-python. No broker, no database: the factories are
exercised only up to the client constructor, which is patched.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "platform_workers")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


# ── settings ───────────────────────────────────────────────────────────────

class TestSettings:
    def test_defaults_match_the_old_workers(self, monkeypatch):
        for var in ("KAFKA_BOOTSTRAP_SERVERS", "KAFKA_SASL_USERNAME", "DB_HOST", "DB_PORT", "DB_NAME",
                    "DB_USER", "DB_PASSWORD", "QDRANT_URL", "EMBEDDING_DIM", "LITELLM_BASE_URL",
                    "LITELLM_API_KEY", "KEYCLOAK_URL", "KEYCLOAK_REALM"):
            monkeypatch.delenv(var, raising=False)
        from common.settings import (
            KafkaSettings,
            KeycloakSettings,
            LiteLLMSettings,
            PostgresSettings,
            QdrantSettings,
        )

        assert KafkaSettings().bootstrap_servers == "kafka:9092"
        assert KafkaSettings().sasl_kwargs() == {}
        pg = PostgresSettings()
        # 8300 is the Postgres port in every manifest set; the password has no
        # default, so a worker without DB_PASSWORD fails to connect instead of
        # trying a dev credential.
        assert (pg.host, pg.port, pg.name, pg.user, pg.password) == ("postgres", 8300, "dataagent", "admin", "")
        assert QdrantSettings().url == "http://qdrant:6333"
        assert QdrantSettings().embedding_dim == 1024
        assert LiteLLMSettings().base_url == "http://litellm:4000"
        assert LiteLLMSettings().api_key == "sk-dev"
        assert KeycloakSettings().realm == "aeroflow"

    def test_env_names_are_the_compose_contract(self, monkeypatch):
        """The compose blocks were carried over verbatim; every name they set
        must land on a field."""
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker:19092")
        monkeypatch.setenv("KAFKA_SASL_USERNAME", "memory-consumer")
        monkeypatch.setenv("KAFKA_SASL_PASSWORD", "pw")
        monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
        monkeypatch.setenv("KAFKA_SASL_MECHANISM", "SCRAM-SHA-512")
        monkeypatch.setenv("DB_HOST", "db.internal")
        monkeypatch.setenv("DB_PORT", "6432")
        monkeypatch.setenv("DB_NAME", "audit")
        monkeypatch.setenv("DB_USER", "svc")
        monkeypatch.setenv("DB_PASSWORD", "s3cret")
        monkeypatch.setenv("QDRANT_URL", "http://vectors:6333")
        monkeypatch.setenv("EMBEDDING_DIM", "4")
        monkeypatch.setenv("LITELLM_BASE_URL", "http://llm:4000")
        monkeypatch.setenv("LITELLM_API_KEY", "sk-test")
        monkeypatch.setenv("KEYCLOAK_URL", "http://kc:8080")
        monkeypatch.setenv("KEYCLOAK_REALM", "tenant-a")
        monkeypatch.setenv("KEYCLOAK_ADMIN", "root")
        monkeypatch.setenv("KEYCLOAK_ADMIN_PASSWORD", "rootpw")
        from common.settings import (
            KafkaSettings,
            KeycloakSettings,
            LiteLLMSettings,
            PostgresSettings,
            QdrantSettings,
        )

        kafka = KafkaSettings()
        assert kafka.bootstrap_servers == "broker:19092"
        assert kafka.sasl_kwargs() == {
            "security_protocol": "SASL_SSL",
            "sasl_mechanism": "SCRAM-SHA-512",
            "sasl_plain_username": "memory-consumer",
            "sasl_plain_password": "pw",
        }
        assert PostgresSettings().connect_kwargs() == {
            "host": "db.internal", "port": 6432, "dbname": "audit", "user": "svc", "password": "s3cret",
        }
        assert QdrantSettings().url == "http://vectors:6333"
        assert QdrantSettings().embedding_dim == 4
        assert LiteLLMSettings().base_url == "http://llm:4000"
        assert LiteLLMSettings().api_key == "sk-test"
        kc = KeycloakSettings()
        assert (kc.url, kc.realm, kc.admin, kc.admin_password) == ("http://kc:8080", "tenant-a", "root", "rootpw")

    def test_worker_settings_keep_their_env_names(self, monkeypatch):
        monkeypatch.setenv("KAFKA_TOPIC", "memory.custom")
        monkeypatch.setenv("KAFKA_DLQ_TOPIC", "memory.custom.dlq")
        monkeypatch.setenv("KAFKA_GROUP_ID", "custom-group")
        monkeypatch.setenv("KAFKA_DDL_TOPIC", "ddl.custom")
        monkeypatch.setenv("CRAWL_INTERVAL_SECONDS", "60")
        monkeypatch.setenv("DDL_DEBOUNCE_SECONDS", "7")
        monkeypatch.setenv("MAX_PENDING_DDL_EVENTS", "9")
        monkeypatch.setenv("KAFKA_DATA_ACCESS_TOPIC", "audit.custom")
        monkeypatch.setenv("POLL_INTERVAL_SECONDS", "3")
        monkeypatch.setenv("STATE_FILE", "/tmp/state.txt")
        from workers.audit.consumer import Settings as AuditSettings
        from workers.memory.worker import Settings as MemorySettings
        from workers.schema_crawler.worker import Settings as CrawlerSettings

        mem = MemorySettings()
        assert (mem.topic, mem.dlq_topic, mem.group_id) == ("memory.custom", "memory.custom.dlq", "custom-group")
        crawler = CrawlerSettings()
        assert crawler.ddl_topic == "ddl.custom"
        assert crawler.group_id == "custom-group"
        assert (crawler.crawl_interval_seconds, crawler.ddl_debounce_seconds, crawler.max_pending_ddl_events) == (60, 7, 9)
        audit = AuditSettings()
        assert audit.data_access_topic == "audit.custom"
        assert audit.poll_interval_seconds == 3
        assert audit.state_file == "/tmp/state.txt"


# ── kafka factories ─────────────────────────────────────────────────────────

class TestKafkaFactories:
    def test_consumer_passes_sasl_kwargs_and_manual_commit(self, monkeypatch):
        from common import kafka as kf
        from common.settings import KafkaSettings

        ctor = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(kf, "KafkaConsumer", ctor)
        settings = KafkaSettings(bootstrap_servers="b:9092", sasl_username="u", sasl_password="p")

        out = kf.consumer(["t1", "t2"], "g", auto_offset_reset="latest", settings=settings)

        assert out is ctor.return_value
        args, kwargs = ctor.call_args
        assert args == ("t1", "t2")
        assert kwargs["bootstrap_servers"] == "b:9092"
        assert kwargs["group_id"] == "g"
        assert kwargs["auto_offset_reset"] == "latest"
        assert kwargs["enable_auto_commit"] is False
        assert kwargs["value_deserializer"] is kf._safe_json
        assert kwargs["security_protocol"] == "SASL_PLAINTEXT"
        assert kwargs["sasl_mechanism"] == "PLAIN"
        assert kwargs["sasl_plain_username"] == "u"
        assert kwargs["sasl_plain_password"] == "p"

    def test_no_username_means_no_sasl_kwargs(self, monkeypatch):
        from common import kafka as kf
        from common.settings import KafkaSettings

        ctor = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(kf, "KafkaProducer", ctor)

        kf.producer(KafkaSettings(bootstrap_servers="b:9092", sasl_username=""))

        kwargs = ctor.call_args.kwargs
        assert "sasl_plain_username" not in kwargs
        assert "security_protocol" not in kwargs
        assert kwargs["acks"] == "all"

    def test_best_effort_consumer_returns_none_when_no_broker(self, monkeypatch):
        """schema-crawler's try_kafka_consumer semantics: cron must keep working."""
        from common import kafka as kf
        from common.settings import KafkaSettings
        from kafka.errors import NoBrokersAvailable

        ctor = MagicMock(side_effect=NoBrokersAvailable())
        monkeypatch.setattr(kf, "KafkaConsumer", ctor)

        out = kf.consumer(["t"], "g", settings=KafkaSettings(), required=False, attempts=2, wait_seconds=0)

        assert out is None
        assert ctor.call_count == 2

    def test_required_consumer_raises_when_no_broker(self, monkeypatch):
        from common import kafka as kf
        from common.settings import KafkaSettings
        from kafka.errors import NoBrokersAvailable

        monkeypatch.setattr(kf, "KafkaConsumer", MagicMock(side_effect=NoBrokersAvailable()))

        with pytest.raises(RuntimeError, match="Kafka unavailable"):
            kf.consumer(["t"], "g", settings=KafkaSettings(), attempts=2, wait_seconds=0)

    def test_safe_json_tolerates_garbage(self):
        from common.kafka import _safe_json

        assert _safe_json(b"") is None
        assert _safe_json(b"\xff\xfe") is None
        assert _safe_json(b"not json") is None
        assert _safe_json(b'{"a": 1}') == {"a": 1}


# ── entrypoint ──────────────────────────────────────────────────────────────

class TestEntrypoint:
    def test_unknown_worker_exits_2(self, monkeypatch, capsys):
        import main as entry

        monkeypatch.setenv("WORKER", "nope")
        assert entry.main() == 2
        assert "nope" in capsys.readouterr().err

    def test_known_worker_dispatches_to_its_package(self, monkeypatch):
        import main as entry

        called = []
        fake = MagicMock(run=lambda: called.append("audit"))
        monkeypatch.setattr(
            entry.importlib,
            "import_module",
            lambda name: fake if name == "services.platform_workers.workers.audit" else None,
        )
        monkeypatch.setenv("WORKER", "audit")

        assert entry.main() == 0
        assert called == ["audit"]


# ── audit bridge supervisor ─────────────────────────────────────────────────

class TestBridgeSupervisor:
    def test_dead_thread_is_restarted_after_the_delay(self):
        from workers.audit.consumer import BridgeSupervisor

        bridge = MagicMock()
        sup = BridgeSupervisor(bridge, restart_delay=5)
        sup.thread = MagicMock(is_alive=MagicMock(return_value=False))
        starts = []
        sup.start = lambda: starts.append(1)  # type: ignore[method-assign]

        sup.check(now=100.0)      # notices the death, arms the delay
        assert starts == []
        sup.check(now=103.0)      # too early
        assert starts == []
        sup.check(now=105.0)      # delay elapsed
        assert starts == [1]

    def test_alive_thread_is_left_alone(self):
        from workers.audit.consumer import BridgeSupervisor

        sup = BridgeSupervisor(MagicMock())
        sup.thread = MagicMock(is_alive=MagicMock(return_value=True))
        sup.start = MagicMock()  # type: ignore[method-assign]

        sup.check(now=1.0)

        sup.start.assert_not_called()
