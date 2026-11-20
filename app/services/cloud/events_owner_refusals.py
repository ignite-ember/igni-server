"""The answer when a person's events are owned elsewhere, or the owner asked for is not allowed.

A 409 with the stable token at the top of the body, where igni on the
desktop and the shell read it (igni-core `docs/design/local-cloud-switch.md`
§2.1, §7): `{"error": "mode_forbids"}`, or `{"error":
"events_owned_elsewhere", "owner": "cloud"}`; `detail` is the sentence.
"""

from __future__ import annotations

from fastapi import status
from fastapi.responses import JSONResponse

from app.services.cloud.events_owner_errors import EventsOwnedElsewhere, EventsOwnerRefused


class EventsOwnerRefusals:
    @staticmethod
    def response(exc: EventsOwnerRefused) -> JSONResponse:
        body: dict[str, str] = {'error': exc.error}
        if isinstance(exc, EventsOwnedElsewhere):
            body['owner'] = exc.owner
        body['detail'] = str(exc)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body)
