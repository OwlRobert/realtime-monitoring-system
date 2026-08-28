"""ORM models. Importing this package registers every model on Base.metadata."""

from app.models.user import User, UserRole

__all__ = ["User", "UserRole"]
