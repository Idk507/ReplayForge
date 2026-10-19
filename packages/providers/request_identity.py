"""Serialize once: reservation digest and outbound body refer to identical bytes."""

import hashlib
import json

from packages.contracts.inference import RequestIdentity
from packages.providers.nebius import NebiusConfig, ProbeError


def prepare_request(
    config: NebiusConfig, payload: dict[str, object]
) -> tuple[NebiusConfig, bytes, RequestIdentity]:
    config = NebiusConfig.from_env(
        {
            "NEBIUS_API_KEY": config.api_key.get_secret_value(),
            "NEBIUS_BASE_URL": config.base_url,
            "NEBIUS_MODEL_PRIMARY": config.model,
        }
    )
    if (
        payload.get("model") != config.model
        or payload.get("max_completion_tokens") != 1200
    ):
        raise ProbeError("MODEL_REQUEST_PROFILE_DENIED")
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return (
        config,
        body,
        RequestIdentity(
            request_ref="sha256:" + hashlib.sha256(body).hexdigest(),
            model=config.model,
            base_url=config.base_url,
        ),
    )
