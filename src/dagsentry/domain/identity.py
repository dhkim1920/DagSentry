"""Identity values shared by persistence and application services."""

from enum import StrEnum


class UserRole(StrEnum):
    """Human principal authorization role."""

    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class UserStatus(StrEnum):
    """Whether a human principal may authenticate."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
