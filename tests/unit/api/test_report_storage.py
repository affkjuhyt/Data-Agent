from services.api.modules.workspace.report_storage import (
    UnconfiguredReportStore,
    report_store,
)


def test_report_store_is_unconfigured_without_minio_secret(monkeypatch):
    monkeypatch.delenv("MINIO_URL", raising=False)

    assert isinstance(report_store(), UnconfiguredReportStore)
