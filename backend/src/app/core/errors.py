from __future__ import annotations


class DomainError(Exception):
    """Base class for service-layer errors that routes map to HTTP responses."""


class MarketNotFound(DomainError):
    pass


class MarketNotOpen(DomainError):
    pass


class InvalidOrder(DomainError):
    pass


class InsufficientBalance(DomainError):
    pass


class InsufficientInventory(DomainError):
    pass


class OrderNotFound(DomainError):
    pass


class OrderNotCancelable(DomainError):
    pass


class ForbiddenOrderAccess(DomainError):
    pass


class MarketAlreadyResolved(DomainError):
    def __init__(self, current_outcome: str):
        self.current_outcome = current_outcome
        super().__init__(f"market already resolved to {current_outcome}")
