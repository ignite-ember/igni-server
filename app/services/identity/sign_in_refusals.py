"""The portal's answer when a sign-in to a registered client is refused.

`PortalSignIn` raises typed errors and decides no status code. This is
the one place they become HTTP responses. An unapproved client or an
unregistered redirect is refused here and never redirected to.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.services.identity.portal_sign_in import (
    ChallengeMethodRefused,
    ClientNotApproved,
    NotAnOrganisationMember,
    RedirectNotRegistered,
)


class SignInRefusals:
    """`with SignInRefusals(): ...` turns a sign-in refusal into the portal's 400, 403 or 404."""

    def __enter__(self) -> SignInRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, ClientNotApproved):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if isinstance(exc, RedirectNotRegistered | ChallengeMethodRefused):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if isinstance(exc, NotAnOrganisationMember):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
