"""Tests for the SSL-verify resolution policy.

The previous implementation silently fell back to ``verify=False`` when the
expected cert file was missing — meaning a Helm misconfiguration produced a
working-looking but MITM-vulnerable deployment. The new policy fails loud:
verification on by default, custom CA only if the file actually exists,
explicit-opt-out only when ``SSL_VERIFY=false`` is set deliberately.
"""

import pytest

from app.config.settings import resolve_ssl_verify


class _FakeSettings:
    def __init__(self, ssl_verify: bool, ssl_cert_file: str | None):
        self.ssl_verify = ssl_verify
        self.ssl_cert_file = ssl_cert_file


def test_explicit_opt_out_returns_false():
    """SSL_VERIFY=false in dev — explicit, logged loudly, allowed."""
    out = resolve_ssl_verify(_FakeSettings(ssl_verify=False, ssl_cert_file=None))
    assert out is False


def test_default_uses_system_ca_trust_store():
    """SSL_VERIFY=true with no custom CA → True (system trust store).
    This is the production-grade default for public-CA Keycloak (Let's Encrypt).
    """
    out = resolve_ssl_verify(_FakeSettings(ssl_verify=True, ssl_cert_file=None))
    assert out is True


def test_empty_cert_file_treated_as_unset():
    """`.env.example` uses `SSL_CERT_FILE=` (empty) as the no-custom-CA
    placeholder; that must round-trip the same as not setting the var."""
    out = resolve_ssl_verify(_FakeSettings(ssl_verify=True, ssl_cert_file=""))
    assert out is True


def test_existing_cert_file_returned_as_path(tmp_path):
    cert = tmp_path / "ca.pem"
    cert.write_text("dummy cert contents")
    out = resolve_ssl_verify(_FakeSettings(ssl_verify=True, ssl_cert_file=str(cert)))
    assert out == str(cert)


def test_missing_cert_file_raises_loudly(tmp_path):
    """The original CVE: a configured-but-missing cert path silently
    downgraded to verify=False. Now it raises at startup."""
    missing = tmp_path / "does-not-exist.pem"
    with pytest.raises(RuntimeError, match="does not exist"):
        resolve_ssl_verify(_FakeSettings(ssl_verify=True, ssl_cert_file=str(missing)))


def test_missing_cert_file_takes_precedence_over_explicit_opt_out():
    """Sanity: SSL_VERIFY=false short-circuits before we look at the cert
    path, so a stale SSL_CERT_FILE pointer doesn't block dev work."""
    out = resolve_ssl_verify(
        _FakeSettings(ssl_verify=False, ssl_cert_file="/nonexistent/path.pem")
    )
    assert out is False
