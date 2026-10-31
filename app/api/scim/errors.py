"""One refusal shape for the whole SCIM surface.

RFC 7644 §3.12 defines the error body a provider expects. A provider
handed FastAPI's `{"detail": …}` instead logs an unparseable response
and tells its administrator nothing useful — which is why both the
router and the token guard built the SCIM shape by hand, and why they
built it twice:

  - `users._error` used the imported `ERROR_SCHEMA` constant and could
    add `scimType`;
  - `scim_token._refuse` hardcoded the URN string and could not.

`ScimError` has been in `app/api/base_v1/schema/scim.py` the whole
time and neither used it.
"""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException

from app.api.base_v1.schema.scim import ScimError

#: RFC 7644 §3.12's typed reasons, narrowed to the ones this
#: implementation can actually produce.
ScimType = Literal['uniqueness', 'invalidValue', 'invalidFilter']


def scim_error(detail: str, code: int, scim_type: ScimType | None = None) -> HTTPException:
    """A refusal in SCIM's own error shape.

    `exclude_none` matters: `scimType` is optional in the RFC, and
    sending it as `null` is not the same as omitting it — some
    providers treat the key's presence as the typed reason.
    """
    body = ScimError(detail=detail, status=str(code), scimType=scim_type)
    return HTTPException(status_code=code, detail=body.model_dump(exclude_none=True))
