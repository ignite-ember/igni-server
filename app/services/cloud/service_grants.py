"""What a service may send a producer's agent, read off the extension it ships (`docs/service-identity.md` §15)."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.db.sql.models.service_extension import ServiceExtension
from app.db.sql.service_extensions import ServiceExtensionDB
from app.security.service_caller import ServiceCaller
from app.services.identity.impact import ExtensionImpact


class ServiceGrants:
    """What a service may send beyond a plain event: its extension's `permissions.service`.

    Declared in the manifest, approved with the service, checked here on
    every request — never left to the producer's igni alone.
    """

    @staticmethod
    def of(extension: ServiceExtension) -> frozenset[str]:
        return frozenset(ExtensionImpact.model_validate(extension.impact or {}).service)

    @classmethod
    def require(cls, extension: ServiceExtension, needed: set[str]) -> None:
        missing = sorted(needed - cls.of(extension))
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f'{extension.extension_id} does not declare '
                + ', '.join(f'service.{grant}' for grant in missing)
                + ' in permissions.service, so its service may not send this',
            )


class ServiceExtensionOf:
    """The extension a calling service ships, recorded by igni, never taken from the request."""

    @staticmethod
    async def caller(caller: ServiceCaller) -> ServiceExtension:
        extension = await ServiceExtensionDB().of(caller.client_id)
        if extension is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='This service has no extension uploaded, so an item has nowhere to go',
            )
        return extension
