"""The shared drivers fail closed on TLS (libs/source_drivers, GEN-12): no sslmode is
verify-full, and a driver that cannot verify a certificate refuses to pretend it does."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from libs import source_drivers

pytestmark = [pytest.mark.unit]


def source(kind: str, options: dict[str, Any] | None, host: str = "db.example.com") -> Any:
    return SimpleNamespace(kind=kind, host=host, port=None, database_name="d", conn_options=options)


def test_no_sslmode_is_verify_full_but_the_in_cluster_demo() -> None:
    assert source_drivers.ssl_mode(source("mysql", None)) == "verify-full"
    assert source_drivers.ssl_mode(source("mysql", {"sslmode": "disable"})) == "disable"
    assert source_drivers.ssl_mode(source("mysql", None, "pg.data.svc.cluster.local")) == "disable"
    with pytest.raises(source_drivers.DriverError):
        source_drivers.ssl_mode(source("mysql", {"sslmode": "allow"}))


@pytest.mark.parametrize("options", [None, {"sslmode": "verify-full"}, {"sslmode": "verify-ca"}])
def test_sql_server_refuses_a_verify_mode_it_cannot_honour(options: dict[str, Any] | None) -> None:
    with pytest.raises(source_drivers.DriverError, match="cannot verify"):
        source_drivers.sqlserver(source("sqlserver", options), "u", "p", "SELECT 1", 5)


def test_mysql_checks_the_certificate_in_every_verify_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import ssl

    full = source_drivers._mysql_tls("verify-full", None)
    assert full["verify_mode"] == ssl.CERT_REQUIRED and full["check_hostname"] is True
    assert full["ca"]  # the system bundle: PyMySQL checks nothing without one
    with_ca = source_drivers._mysql_tls("require", "/etc/ssl/custom.pem")
    assert with_ca == {"ca": "/etc/ssl/custom.pem", "verify_mode": ssl.CERT_REQUIRED, "check_hostname": False}
    assert source_drivers._mysql_tls("require", None)["verify_mode"] == ssl.CERT_NONE
    monkeypatch.setattr(
        ssl, "get_default_verify_paths", lambda: type("P", (), {"cafile": None})()
    )
    with pytest.raises(source_drivers.DriverError, match="ssl_ca"):
        source_drivers._mysql_tls("verify-ca", None)
