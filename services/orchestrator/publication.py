"""Approval-bound publication orchestration; no model or unauthenticated API route."""

from collections.abc import Callable
from typing import Protocol, Self
from uuid import UUID

from services.approval.journal import ApprovalJournal, PublicationIntent
from services.approval.policy import ApprovalBinding, ReviewPermission
from services.persistence.store import Store, StoreError
from services.publication.github import PublicationWork, PullRequestResult
from services.publication.journal import PublicationJournal


class PublisherPort(Protocol):
    origin: str

    def __enter__(self) -> Self: ...
    def __exit__(self, *_: object) -> None: ...
    def publish(
        self, work: PublicationWork, authorize: Callable[[], PublicationIntent]
    ) -> PullRequestResult: ...


def publish_approved(
    store: Store,
    binding: ApprovalBinding,
    nonce: UUID,
    permission_loader: Callable[[], ReviewPermission],
    publisher_factory: Callable[[], PublisherPort],
    *,
    expected_version: int,
    allow_live_writes: bool = False,
) -> PullRequestResult:
    """Recheck real human membership/approval before each write, not just job start.

    Both factories are trusted deployment dependencies, never request payloads.
    Unknown provider outcomes leave the durable intent available for discovery.
    Test HTTP can exercise the adapter but cannot persist a real PR_OPENED state.
    """
    if type(expected_version) is not int:
        raise StoreError("PUBLICATION_VERSION_INVALID")
    journal = PublicationJournal(store)
    permission = permission_loader()
    cached = journal.get(binding, nonce, permission)
    if cached is not None:
        return cached
    approval = ApprovalJournal(store, target_branch=binding.target_branch)
    intent = approval.claim(
        binding, permission, nonce=nonce, expected_version=expected_version
    )
    work = journal.work(intent, permission, expected_version=expected_version)

    def authorize() -> PublicationIntent:
        current = permission_loader()
        checked = approval.claim(
            binding, current, nonce=nonce, expected_version=expected_version
        )
        if store.repository(binding.repository_id, binding.owner_id) != work.grant:
            raise StoreError("PUBLICATION_SCOPE_CHANGED")
        return checked

    with publisher_factory() as publisher:
        if publisher.origin not in {"REMOTE_SERVICE", "TEST_TRANSPORT"}:
            raise StoreError("PUBLICATION_BACKEND_INVALID")
        if publisher.origin == "REMOTE_SERVICE" and not allow_live_writes:
            raise StoreError("PUBLICATION_LIVE_NOT_AUTHORIZED")
        result = publisher.publish(work, authorize)
        if result.origin != publisher.origin:
            raise StoreError("PUBLICATION_ORIGIN_MISMATCH")
    return journal.complete(intent, result, expected_version=expected_version)
