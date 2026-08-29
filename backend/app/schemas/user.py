from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole

# bcrypt only reads the first 72 bytes of a password; refuse longer input
# outright rather than silently truncating it.
MAX_PASSWORD_LENGTH = 72


class UserCreate(BaseModel):
    """Registration request. The role is assigned by the server, never the client."""

    username: str = Field(min_length=3, max_length=50)
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.lower()


class UserRead(BaseModel):
    """Safe user representation. Deliberately has no password field of any kind."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime
