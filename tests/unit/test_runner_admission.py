"""Ephemeral capability signer only; no live runner facts are asserted."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from services.runner.admission import (
    REQUIRED_CONTROLS,
    AdmissionDenied,
    CapabilityAttestation,
    CapabilityVerifier,
    ControlEvidence,
    RunnerCapabilities,
    RunnerLimits,
    recheck_retained_capability,
)
from services.runner.commands import plan_pytest
from services.runner.receipts import canonical
from tests.unit.test_command_policy import inputs


def fixture(origin="TEST_TRANSPORT"):
    run, grant, owner, request = inputs()
    plan = plan_pytest(request, run, grant, owner)
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    profile = RunnerCapabilities(
        runner_id=uuid4(),
        backend="NEBIUS_CONTREE",
        image_digest=request.image_digest,
        configuration_ref="sha256:" + "c" * 64,
        origin=origin,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        controls=tuple(
            ControlEvidence(control=c, status="PASS", evidence_ref="sha256:" + "a" * 64)
            for c in REQUIRED_CONTROLS
        ),
        limits=RunnerLimits(
            cpu_millis=1000,
            memory_bytes=512 * 1024 * 1024,
            pids=64,
            wall_seconds=60,
            writable_bytes=64 * 1024 * 1024,
            output_bytes=131072,
            file_count=1024,
        ),
    )

    def sign(payload=profile, key_id="test"):
        unsigned = CapabilityAttestation(
            key_id=key_id, payload=payload, signature="0" * 128
        )
        return unsigned.model_copy(
            update={"signature": key.sign(canonical(unsigned, unsigned=True)).hex()}
        )

    verifier = CapabilityVerifier(
        {"test": key.public_key()},
        runner_id=profile.runner_id,
        backend="NEBIUS_CONTREE",
        origin=origin,
        configuration_ref=profile.configuration_ref,
    )
    return plan, profile, now, sign, verifier


def test_synthetic_capabilities_cannot_qualify_a_remote_runner() -> None:
    plan, _, now, sign, verifier = fixture()
    result = verifier.assess(sign(), plan, now=now)
    assert result.status == "DENIED"
    assert "TEST_EVIDENCE" in result.reasons
    assert result.can_authorize_execution is False


def test_remote_claim_contract_only_satisfies_capability_gate() -> None:
    # This signs invented contract data; it is not live acceptance evidence.
    plan, _, now, sign, verifier = fixture("REMOTE_SERVICE")
    result = verifier.assess(sign(), plan, now=now)
    assert result.status == "CAPABILITY_REQUIREMENTS_MET"
    assert result.can_authorize_execution is False
    assert result.reasons == ()


def test_retained_capability_is_rehashed_and_reverified():
    from services.runner.receipts import canonical

    plan, _, now, sign, verifier = fixture("REMOTE_SERVICE")
    payload = canonical(sign())
    reference = "sha256:" + hashlib.sha256(payload).hexdigest()
    attestation, decision = recheck_retained_capability(
        payload, reference, plan, verifier, now=now
    )
    assert attestation.payload.origin == "REMOTE_SERVICE"
    assert decision.status == "CAPABILITY_REQUIREMENTS_MET"
    assert decision.attestation_ref == reference


@pytest.mark.parametrize("fault", ["digest", "signature", "test_origin"])
def test_retained_capability_fails_closed(fault):
    from services.runner.receipts import canonical

    plan, _, now, sign, verifier = fixture(
        "TEST_TRANSPORT" if fault == "test_origin" else "REMOTE_SERVICE"
    )
    attestation = sign()
    if fault == "signature":
        attestation = attestation.model_copy(update={"signature": "0" * 128})
    payload = canonical(attestation)
    reference = "sha256:" + hashlib.sha256(payload).hexdigest()
    if fault == "digest":
        reference = "sha256:" + "0" * 64
    with pytest.raises(AdmissionDenied):
        recheck_retained_capability(payload, reference, plan, verifier, now=now)


@pytest.mark.parametrize("control", REQUIRED_CONTROLS)
@pytest.mark.parametrize("status", ["FAIL", "UNKNOWN", "OMITTED"])
def test_each_required_control_fails_closed(control: str, status: str) -> None:
    plan, profile, now, sign, verifier = fixture("REMOTE_SERVICE")
    controls = tuple(
        c
        if c.control != control
        else c.model_copy(update={"status": status, "evidence_ref": None})
        for c in profile.controls
        if c.control != control or status != "OMITTED"
    )
    result = verifier.assess(
        sign(profile.model_copy(update={"controls": controls})), plan, now=now
    )
    assert result.status == "DENIED"
    assert control in result.missing_controls


@pytest.mark.parametrize(
    "field,value",
    [
        ("cpu_millis", 2001),
        ("memory_bytes", 512 * 1024 * 1024 + 1),
        ("pids", 65),
        ("wall_seconds", 61),
        ("writable_bytes", 64 * 1024 * 1024 + 1),
        ("output_bytes", 131073),
        ("file_count", 1025),
    ],
)
def test_enforced_limits_cannot_exceed_policy(field: str, value: int) -> None:
    plan, profile, now, sign, verifier = fixture("REMOTE_SERVICE")
    profile = profile.model_copy(
        update={"limits": profile.limits.model_copy(update={field: value})}
    )
    result = verifier.assess(sign(profile), plan, now=now)
    assert result.status == "DENIED"
    assert "LIMITS_EXCEED_POLICY" in result.reasons


@pytest.mark.parametrize(
    "fault",
    [
        "signature",
        "unknown_key",
        "image",
        "runner",
        "origin",
        "backend",
        "expired",
        "future",
        "lifetime",
        "naive_clock",
    ],
)
def test_forgery_binding_and_freshness_denied(fault: str) -> None:
    plan, profile, now, sign, verifier = fixture()
    if fault == "image":
        profile = profile.model_copy(update={"image_digest": "sha256:" + "b" * 64})
    if fault == "runner":
        profile = profile.model_copy(update={"runner_id": uuid4()})
    if fault == "origin":
        profile = profile.model_copy(update={"origin": "REMOTE_SERVICE"})
    if fault == "backend":
        profile = profile.model_copy(update={"backend": "REMOTE_LINUX"})
    if fault == "expired":
        now += timedelta(hours=2)
    if fault == "future":
        now -= timedelta(seconds=6)
    if fault == "lifetime":
        profile = profile.model_copy(update={"expires_at": now + timedelta(hours=25)})
    if fault == "naive_clock":
        now = now.replace(tzinfo=None)
    signed = sign(profile, "missing" if fault == "unknown_key" else "test")
    if fault == "signature":
        signed = signed.model_copy(update={"signature": "0" * 128})
    with pytest.raises(AdmissionDenied):
        verifier.assess(signed, plan, now=now)


@pytest.mark.parametrize(
    "fault",
    ["duplicate", "pass_without_evidence", "extra_log", "authority", "boolean_limit"],
)
def test_closed_profiles_reject_unsafe_metadata(fault: str) -> None:
    _, profile, _, sign, _ = fixture()
    data = profile.model_dump()
    if fault == "duplicate":
        data["controls"] = (*profile.controls, profile.controls[0])
    if fault == "pass_without_evidence":
        data["controls"] = (dict(control=REQUIRED_CONTROLS[0], status="PASS"),)
    if fault == "extra_log":
        data["stdout"] = "secret"
    if fault == "boolean_limit":
        data["limits"]["pids"] = True
    if fault == "authority":
        data = {**sign().model_dump(), "can_authorize_execution": True}
    with pytest.raises(ValidationError):
        (
            CapabilityAttestation if fault == "authority" else RunnerCapabilities
        ).model_validate(data)


def test_missing_limits_and_empty_capabilities_are_denied() -> None:
    plan, profile, now, sign, verifier = fixture("REMOTE_SERVICE")
    result = verifier.assess(
        sign(profile.model_copy(update={"controls": (), "limits": None})), plan, now=now
    )
    assert result.status == "DENIED"
    assert result.missing_controls == REQUIRED_CONTROLS
    assert set(result.reasons) == {"MISSING_CONTROLS", "MISSING_LIMITS"}


@pytest.mark.parametrize("keys", [{}, {"bad key": None}, {1: None}])
def test_invalid_trust_registry_is_denied(keys) -> None:
    with pytest.raises(AdmissionDenied, match="CAPABILITY_CONFIG_INVALID"):
        CapabilityVerifier(
            keys,
            runner_id=uuid4(),
            backend="NEBIUS_CONTREE",
            origin="TEST_TRANSPORT",
            configuration_ref="sha256:" + "c" * 64,
        )


def test_baseline_receipt_purpose_cannot_be_replayed_as_capabilities() -> None:
    plan, _, now, sign, verifier = fixture()
    forged = sign().model_copy(update={"purpose": "replayforge.baseline-receipt.v1"})
    with pytest.raises(AdmissionDenied, match="CAPABILITY_SIGNATURE_INVALID"):
        verifier.assess(forged, plan, now=now)


def test_smoke_pass_is_not_a_capability_attestation() -> None:
    with pytest.raises(ValidationError):
        CapabilityAttestation.model_validate(
            {
                "status": "PASSED",
                "image_uuid": uuid4(),
                "compute_terminal_confirmed": True,
            }
        )


def test_runtime_configuration_mismatch_is_denied() -> None:
    plan, profile, now, sign, verifier = fixture()
    profile = profile.model_copy(update={"configuration_ref": "sha256:" + "d" * 64})
    with pytest.raises(AdmissionDenied, match="CAPABILITY_BINDING_DENIED"):
        verifier.assess(sign(profile), plan, now=now)
