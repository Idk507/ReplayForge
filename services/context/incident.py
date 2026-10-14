"""Bounded incident preparation; raw input is never returned or persisted."""

import unicodedata

from pydantic import SecretStr

from packages.contracts.incident import IncidentReport
from services.context.collector import CREDENTIAL, PRIVATE_KEY


def prepare_incident(
    text: str, *, known_secrets: tuple[SecretStr, ...] = ()
) -> IncidentReport:
    """Redact before truncation; patterns are not a complete DLP guarantee."""
    try:
        if not 1 <= len(text) <= 20000 or len(text.encode()) > 65536:
            raise ValueError("INCIDENT_INPUT_LIMIT")
    except UnicodeEncodeError:
        raise ValueError("INCIDENT_INPUT_ENCODING") from None
    if len(known_secrets) > 32:
        raise ValueError("INCIDENT_SECRET_CONFIG")

    def normalize(value: str) -> str:
        return "".join(
            c
            for c in value
            if c in "\n\t" or not unicodedata.category(c).startswith("C")
        )

    clean = normalize(text)
    secrets = tuple(normalize(s.get_secret_value()) for s in known_secrets)
    if any(not 1 <= len(s) <= 4096 for s in secrets):
        raise ValueError("INCIDENT_SECRET_CONFIG")
    for secret in sorted(secrets, key=len, reverse=True):
        clean = clean.replace(secret, "[REDACTED]")
    clean = CREDENTIAL.sub("[REDACTED]", PRIVATE_KEY.sub("[REDACTED]", clean))
    return IncidentReport(text=clean[:1024], truncated=len(clean) > 1024)
