"""Admission is bounded server policy, never instructions from repository text."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts.repository import RepositoryGrant
from packages.policies.repository import admit, allowed_entry


def grant(**updates: object) -> RepositoryGrant:
    data: dict[str, object] = dict(
        repository_id=uuid4(),
        owner_id=uuid4(),
        remote_url="https://github.com/example/fixture",
        commits=("a" * 40,),
        approved_paths=("src/", "tests/", "pyproject.toml"),
        consent=True,
    )
    data.update(updates)
    return RepositoryGrant.model_validate(data)


def test_exact_caller_repo_commit_required() -> None:
    policy = grant()
    assert admit(policy, policy.owner_id, policy.repository_id, "a" * 40).allowed
    for caller, repository, commit in (
        (uuid4(), policy.repository_id, "a" * 40),
        (policy.owner_id, uuid4(), "a" * 40),
        (policy.owner_id, policy.repository_id, "b" * 40),
    ):
        assert not admit(policy, caller, repository, commit).allowed


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/repo",
        "git@github.com:x/y",
        "https://github.com.evil.test/x/y",
        "https://user:password@github.com/x/y",
        "https://github.com/x/y?token=secret",
        "https://github.com/x/../y",
        "https://github.com/x/y.git",
        "https://127.0.0.1/x/y",
    ],
)
def test_unsafe_origin_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        grant(remote_url=url)


def test_private_requires_explicit_private_permission() -> None:
    policy = grant(private=True)
    assert not admit(policy, policy.owner_id, policy.repository_id, "a" * 40).allowed
    policy = grant(private=True, private_access_approved=True)
    assert admit(policy, policy.owner_id, policy.repository_id, "a" * 40).allowed


def test_no_consent_cannot_be_overridden() -> None:
    policy = grant(consent=False)
    decision = admit(policy, policy.owner_id, policy.repository_id, "a" * 40)
    assert not decision.allowed and not decision.approved_paths


@pytest.mark.parametrize(
    "path",
    [
        "../src/a.py",
        "/src/a.py",
        "src\\a.py",
        "src/../a.py",
        "src//a.py",
        "src/a.py:secret",
        "src/CON.py",
        "src/a.py.",
        "src/.env",
        "src/.git/config",
        ".github/workflows/ci.yml",
        "src/key.pem",
        "src/%2e%2e/a.py",
        "src/a\x00.py",
        "src/COM1.txt",
    ],
)
def test_malicious_paths_rejected(path: str) -> None:
    assert not allowed_entry(grant(), path, "100644")


@pytest.mark.parametrize("mode", ["120000", "160000", "040000", "unknown"])
def test_symlinks_submodules_and_unknown_entries_rejected(mode: str) -> None:
    assert not allowed_entry(grant(), "src/a.py", mode)


def test_paths_are_component_scoped() -> None:
    policy = grant()
    assert allowed_entry(policy, "src/nested/a.py", "100644")
    assert allowed_entry(policy, "pyproject.toml", "100644")
    assert not allowed_entry(policy, "src-other/a.py", "100644")
    assert not allowed_entry(policy, "README.md", "100644")


def test_checkout_root_is_an_explicit_registered_path() -> None:
    policy = grant(approved_paths=("checkout/",))
    assert allowed_entry(policy, "checkout/payments.py", "100644")
    assert not allowed_entry(policy, "checkout-other/payments.py", "100644")
    assert not allowed_entry(policy, "checkout/../payments.py", "100644")
    with pytest.raises(ValidationError):
        grant(approved_paths=("arbitrary/",))


def test_authenticated_checkout_opt_in_is_strict_and_off_by_default() -> None:
    from services.auth.access import ConnectRepository, approved_repository_paths

    base = {
        "installation_id": 7,
        "repository_id": 42,
        "source_commit": "a" * 40,
        "consent": True,
    }
    assert ConnectRepository(**base).include_checkout is False
    assert ConnectRepository(**base, include_checkout=True).include_checkout is True
    assert "checkout/" not in approved_repository_paths(False)
    assert approved_repository_paths(True).count("checkout/") == 1
    with pytest.raises(ValidationError):
        ConnectRepository(**base, include_checkout=1)


@pytest.mark.parametrize("mode", ["120000", "160000", "040000", "unknown"])
def test_checkout_symlinks_and_nonfiles_are_rejected(mode: str) -> None:
    policy = grant(approved_paths=("checkout/",))
    assert not allowed_entry(policy, "checkout/payments.py", mode)


def test_construct_bypass_revalidated() -> None:
    policy = grant().model_copy(update={"remote_url": "file:///etc"})
    with pytest.raises(ValidationError):
        admit(policy, policy.owner_id, policy.repository_id, "a" * 40)
