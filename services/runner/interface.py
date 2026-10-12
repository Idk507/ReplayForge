"""Narrow M0 runner port; no model-composed command or patch API is exposed."""

from typing import Protocol
from uuid import UUID

from services.runner.responses import Operation


class SandboxBackend(Protocol):
    """Submit one fixed probe and inspect/cancel only owned operation IDs.

    The production create/execute/apply-patch/artifact port is intentionally
    deferred until resource, image and filesystem guarantees are established.
    """

    def start_probe(self) -> UUID:
        """Submit once; an uncertain submission must never be retried blindly."""
        ...

    def inspect(self, operation_id: UUID) -> Operation:
        """Read a correlated observation without executing new guest work."""
        ...

    def terminate(self, operation_id: UUID) -> None:
        """Request cancellation; callers must separately confirm terminal state."""
        ...
