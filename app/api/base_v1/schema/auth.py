import re

from pydantic import BaseModel, EmailStr, field_validator


def _validate_password_strength(password: str) -> str:
    """Enforce minimum password requirements."""
    if len(password) < 8:
        raise ValueError('Password must be at least 8 characters')
    if not re.search(r'[A-Z]', password):
        raise ValueError('Password must contain at least one uppercase letter')
    if not re.search(r'[a-z]', password):
        raise ValueError('Password must contain at least one lowercase letter')
    if not re.search(r'\d', password):
        raise ValueError('Password must contain at least one digit')
    if not re.search(r'[^A-Za-z0-9]', password):
        raise ValueError('Password must contain at least one special character')
    return password


class SignInRequest(BaseModel):
    email: EmailStr
    password: str


class SendSignInCodeRequest(BaseModel):
    email: EmailStr


class SignInCodeRequest(BaseModel):
    email: EmailStr
    code: str


class SendResetPasswordCodeRequest(BaseModel):
    email: EmailStr


class ResetNewPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str

    @field_validator('new_password')
    @classmethod
    def check_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class SendSignUpCodeRequest(BaseModel):
    email: EmailStr


class SignUpRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    code: str
    password: str

    @field_validator('password')
    @classmethod
    def check_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class AuthResponse(BaseModel):
    """The answer to "send me a code" and to "set my password".

    Deliberately carries **no token**. It used to have
    ``access_token: str | None = None``, which nothing ever populated —
    zero call sites — and that is worse than it sounds rather than
    harmless.

    These are the responses to requests made by an anonymous caller who
    has typed in an email address. ``services/email/delivery.py`` explains
    why handing anything back is the wrong shape: "returning the code in
    the response would let anyone request one for any address and read it
    straight back — account takeover, dressed as a convenience feature."
    A nullable ``access_token`` sitting on exactly that response is a
    field pre-shaped for that mistake, waiting for somebody to fill it in
    the week a user complains about an extra step.

    Signing in really does return a token, from ``/sign-in``,
    ``/sign-in-with-code`` and ``/sign-up`` — those answer with
    ``TokenResponse``, which is where a token belongs.
    """

    message: str
