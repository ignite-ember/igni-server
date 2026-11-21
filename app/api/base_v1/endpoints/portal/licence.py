"""The paid modules' licence, on the portal.

* `GET /portal/licence/notice` — anybody signed in: the banner, one
  sentence per module that needs attention, empty when nothing does.
* `GET /portal/licence` — a super admin: the installed file and where
  each module stands.
* `PUT /portal/licence` — a super admin: install a licence file. It is
  verified first, against the keys built into this release, and
  refused with the reason if it is not good. Audited like every portal
  change.

igni server shows and installs the licence; each paid module enforces
it (docs/licence-file.md).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from app.api.base_v1.schema.portal.user import PortalAccount
from app.security.portal_auth import get_current_account
from app.security.super_admin import require_super_admin
from app.services.deployment_licence import (
    deployment_licence,
    DeploymentLicence,
    LicenceNotice,
    LicenceOverview,
    LicencePathReadOnly,
    LicenceRejected,
)
from app.services.licence import LicenceEnvelope

router = APIRouter()


@router.get('/licence/notice', response_model=LicenceNotice)
async def licence_notice(
    _account: PortalAccount = Depends(get_current_account),
    licence: DeploymentLicence = Depends(deployment_licence),
) -> LicenceNotice:
    return licence.notice()


@router.get('/licence', response_model=LicenceOverview)
async def licence_overview(
    _account: PortalAccount = Depends(require_super_admin),
    licence: DeploymentLicence = Depends(deployment_licence),
) -> LicenceOverview:
    return licence.overview()


#: The body is read by hand, so the schema is declared here for the API's description.
_ENVELOPE_BODY = {
    'requestBody': {
        'required': True,
        'content': {'application/json': {'schema': LicenceEnvelope.model_json_schema()}},
    }
}


def _refused(exc: LicenceRejected) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={'reason': exc.reason.value, 'message': str(exc)},
    )


@router.put('/licence', response_model=LicenceOverview, openapi_extra=_ENVELOPE_BODY)
async def install_licence(
    request: Request,
    _account: PortalAccount = Depends(require_super_admin),
    licence: DeploymentLicence = Depends(deployment_licence),
) -> LicenceOverview:
    # Not a `LicenceEnvelope` parameter: FastAPI would answer a body missing a field with its own
    # list of validation errors, where every other refusal is `{reason, message}` with `malformed`
    # (docs/licence-file.md, "From the portal").
    try:
        envelope = LicenceEnvelope.model_validate_json(await request.body())
    except ValidationError as exc:
        raise _refused(licence.reject_malformed()) from exc
    try:
        return licence.install(envelope)
    except LicenceRejected as exc:
        raise _refused(exc) from exc
    except LicencePathReadOnly as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'reason': 'licence_path_read_only',
                'message': f'The licence is valid, but {exc} cannot be written from here. '
                'Install it where the licence is mounted (the licence Secret in Kubernetes) instead.',
            },
        ) from exc
