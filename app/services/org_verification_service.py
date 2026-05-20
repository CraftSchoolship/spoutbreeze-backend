"""DNS TXT record verification for self-serve organization domain claims.

Self-serve org creation hands the admin a token and asks them to publish a
TXT record at ``_bluescale-verify.<domain>`` with the value
``bluescale-verify=<token>``. This module is the one place that talks to DNS.
"""

from __future__ import annotations

import dns.asyncresolver
import dns.exception

from app.config.logger_config import get_logger

logger = get_logger("OrgVerificationService")

VERIFICATION_RECORD_PREFIX = "_bluescale-verify"
VERIFICATION_VALUE_PREFIX = "bluescale-verify="
_DNS_TIMEOUT_SECONDS = 5.0


def verification_record_name(domain: str) -> str:
    """The fully-qualified DNS name the org admin must publish a TXT record at."""
    return f"{VERIFICATION_RECORD_PREFIX}.{domain}"


def verification_record_value(token: str) -> str:
    """The exact TXT record value the org admin must publish."""
    return f"{VERIFICATION_VALUE_PREFIX}{token}"


async def verify_domain_record(domain: str, expected_token: str) -> bool:
    """
    Resolve ``_bluescale-verify.<domain>`` TXT records and look for an exact
    ``bluescale-verify=<expected_token>`` match.

    Returns True on match, False on missing record / NXDOMAIN / timeout — any
    transient or expected failure surfaces as "not verified yet" to the caller.
    """
    record_name = verification_record_name(domain)
    expected_value = verification_record_value(expected_token)
    try:
        answer = await dns.asyncresolver.resolve(
            record_name, "TXT", lifetime=_DNS_TIMEOUT_SECONDS
        )
    except dns.exception.DNSException as e:
        logger.info(f"DNS verification miss for {record_name}: {e.__class__.__name__}")
        return False

    for rdata in answer:
        # dnspython splits TXT into chunks; rejoin before comparing.
        chunks = getattr(rdata, "strings", None) or []
        joined = b"".join(chunks).decode("utf-8", errors="replace")
        if joined == expected_value:
            logger.info(f"DNS verification SUCCESS for {record_name}")
            return True

    logger.info(f"DNS verification record present at {record_name} but token did not match")
    return False
