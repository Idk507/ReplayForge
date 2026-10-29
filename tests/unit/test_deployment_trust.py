"""Deployment trust config checks using disposable, in-memory Ed25519 identities."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.orchestrator import deployment_trust
from services.orchestrator.deployment_trust import (
    DeploymentTrustError,
    build_verification_trust,
    provide_verification_trust,
)


def _public_pem(key: Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


def _fixture():
    now = datetime.now(UTC)
    keys = {
        "gate-202610": Ed25519PrivateKey.generate(),
        "collector-202610": Ed25519PrivateKey.generate(),
        "assessor-202610": Ed25519PrivateKey.generate(),
    }
    validity = {
        "not_before": (now - timedelta(days=1)).isoformat(),
        "not_after": (now + timedelta(days=30)).isoformat(),
    }

    def trust_key(key_id: str) -> dict[str, str]:
        return {
            "key_id": key_id,
            "algorithm": "Ed25519",
            "public_key_pem": _public_pem(keys[key_id]),
            **validity,
        }

    phase = {
        "image_uuid": str(uuid4()),
        "image_digest": "sha256:" + "1" * 64,
        "configuration_ref": "sha256:" + "2" * 64,
        "runner_id": str(uuid4()),
        "backend": "NEBIUS_CONTREE",
        "receipt_keys": [trust_key("collector-202610")],
        "assessor_keys": [trust_key("assessor-202610")],
    }
    config = {
        "schema_version": "replayforge-verification-trust-v1",
        "gate_signer": trust_key("gate-202610"),
        "gate_keys": [trust_key("gate-202610")],
        "phases": {
            phase_name: {
                **phase,
                "runner_id": str(uuid4()),
                "image_uuid": str(uuid4()),
                "configuration_ref": "sha256:" + format(index, "x") * 64,
            }
            for index, phase_name in enumerate(
                ("BASELINE", "CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"), 3
            )
        },
    }
    backend = SimpleNamespace(
        key_id="gate-202610",
        public_key=keys["gate-202610"].public_key(),
        sign_bytes=keys["gate-202610"].sign,
    )
    return config, keys, backend, now


def _build(config, backend, now):
    raw = json.dumps(config, separators=(",", ":")).encode()
    return build_verification_trust(raw, backend, now=now)


def test_deployment_config_builds_existing_remote_trust_types():
    config, _, backend, now = _fixture()
    trust = _build(config, backend, now)

    assert trust.signer.key_id == "gate-202610"
    assert trust.gate_verifier._keys.keys() == {"gate-202610"}
    assert set(trust.phase_trust) == {
        "BASELINE",
        "CANDIDATE",
        "EXISTING_SUITE",
        "CLEAN_REPLAY",
    }
    assert all(
        item.receipt_verifier._origin == "REMOTE_SERVICE"
        and item.capability_verifier._origin == "REMOTE_SERVICE"
        for item in trust.phase_trust.values()
    )


@pytest.mark.parametrize("missing", ("gate_signer", "receipt_keys", "assessor_keys"))
def test_deployment_config_rejects_missing_required_identity(missing):
    config, _, backend, now = _fixture()
    if missing == "gate_signer":
        del config["gate_signer"]
    else:
        phase = config["phases"]["CANDIDATE"]
        phase[missing] = []
    with pytest.raises(DeploymentTrustError):
        _build(config, backend, now)


def test_deployment_config_rejects_signer_key_pair_mismatch():
    config, _, backend, now = _fixture()
    wrong = Ed25519PrivateKey.generate()
    backend.public_key = wrong.public_key()
    with pytest.raises(DeploymentTrustError, match="IDENTITY_MISMATCH"):
        _build(config, backend, now)


def test_deployment_config_rejects_mismatched_signer_key_id():
    config, _, backend, now = _fixture()
    backend.key_id = "gate-other-202610"
    with pytest.raises(DeploymentTrustError, match="IDENTITY_MISMATCH"):
        _build(config, backend, now)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda c, now: c["gate_signer"].update(
            not_after=(now - timedelta(seconds=1)).isoformat()
        ),
        lambda c, now: c["gate_signer"].update(
            not_after=(now + timedelta(days=2)).isoformat()
        ),
        lambda c, now: c["gate_signer"].update(algorithm="RSA"),
        lambda c, now: c["gate_signer"].update(key_id="gate-test-202610"),
        lambda c, now: c["phases"]["CANDIDATE"]["receipt_keys"][0].update(
            key_id="assessor-202610"
        ),
        lambda c, now: c["gate_keys"].append(
            c["phases"]["BASELINE"]["receipt_keys"][0].copy()
        ),
        lambda c, now: c["gate_keys"].append(
            {
                **c["phases"]["BASELINE"]["receipt_keys"][0],
                "key_id": "gate-secondary-202610",
            }
        ),
        lambda c, now: c["phases"]["BASELINE"]["assessor_keys"][0].update(
            public_key_pem=c["phases"]["BASELINE"]["receipt_keys"][0]["public_key_pem"]
        ),
        lambda c, now: c["phases"]["CANDIDATE"]["assessor_keys"][0].update(
            public_key_pem="not a PEM public key"
        ),
    ),
    ids=(
        "expired-key",
        "insufficient-remaining-validity",
        "unsupported-algorithm",
        "test-only-key-id",
        "cross-role-key-id",
        "verifier-collector-key-id-reuse",
        "verifier-collector-key-material-reuse",
        "collector-assessor-key-material-reuse",
        "malformed-public-key",
    ),
)
def test_deployment_config_rejects_unsafe_key_metadata(mutate):
    config, _, backend, now = _fixture()
    mutate(config, now)
    with pytest.raises(DeploymentTrustError):
        _build(config, backend, now)


def test_deployment_config_rejects_duplicate_json_keys():
    config, _, _, _ = _fixture()
    raw = json.dumps(config, separators=(",", ":")).encode()
    duplicate = raw[:-1] + b',"schema_version":"attacker"}'
    with pytest.raises(DeploymentTrustError, match="CONFIG_INVALID"):
        deployment_trust._parse_config(duplicate, now=datetime.now(UTC))


def test_secret_manager_adapter_loads_from_operator_config_file(tmp_path, monkeypatch):
    config, _, backend, _ = _fixture()
    path = tmp_path / "verification-trust.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(
        deployment_trust.importlib,
        "import_module",
        lambda _: SimpleNamespace(provide_signing_backend=lambda: backend),
    )
    trust = provide_verification_trust(
        {
            "REPLAYFORGE_VERIFICATION_TRUST_CONFIG": str(path),
            "REPLAYFORGE_VERIFICATION_SIGNER_PROVIDER": "operator.signer",
        }
    )
    assert trust.signer.key_id == "gate-202610"


def test_provider_rejects_missing_or_symlinked_configuration(tmp_path):
    with pytest.raises(DeploymentTrustError):
        provide_verification_trust({})
    target = tmp_path / "real.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(DeploymentTrustError):
        provide_verification_trust(
            {
                "REPLAYFORGE_VERIFICATION_TRUST_CONFIG": str(link),
                "REPLAYFORGE_VERIFICATION_SIGNER_PROVIDER": "operator.signer",
            }
        )
