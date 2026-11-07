"""Telling every approved service about a person: erasure and export.

GDPR requests arrive here, at the controller's own deployment, and the
data they concern is spread across the services that registered with
it. Each approved service with a `base_url` is asked, for the
organisation whose administrator made the request:

* `DELETE {base_url}/v1/igni/subjects/{user_id}` — erase or redact.
  Must be idempotent: an erasure that half-failed is run again.
* `GET {base_url}/v1/igni/subjects/{user_id}/export` — the service's
  sections of an Article 15 report, as `{"sections": [{"name", "rows"}]}`.

Each call carries a token igni signs for that one service (`aud` = its
URL, `token_use` = `igni`, `org` = the organisation), so a service
checks it against the published keys like any other igni token and
needs no secret shared with this server.

Erasure fails loudly and export degrades: an erasure reported done when
it was not is a legal failure, while an export already has `truncated`
to say what is missing.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

import httpx
from pydantic import BaseModel, Field

from app.db.sql.models.registered_client import RegisteredClient
from app.db.sql.registered_clients import RegisteredClientDB
from app.services.auth.jwt_service import JWTService, TokenUse

logger = logging.getLogger(__name__)

#: Long enough for one call, and no use to anybody afterwards.
CALL_TOKEN_LIFETIME = timedelta(minutes=2)
TIMEOUT_SECONDS = 15.0


class ServiceSection(BaseModel):
    name: str
    rows: list[dict[str, object]] = Field(default_factory=list)


class ServiceExport(BaseModel):
    sections: list[ServiceSection] = Field(default_factory=list)


#: `{service name: {section name: rows}}` — what every registered
#: service answered an export with, keyed by runtime names, so a map.
ServiceSections = dict[str, dict[str, list[dict[str, object]]]]


class ExportAnswers(BaseModel):
    sections: ServiceSections = Field(default_factory=dict)
    #: Services that did not answer, by name.
    missing: list[str] = Field(default_factory=list)


class ErasureIncomplete(RuntimeError):
    """Some services did not confirm the erasure. Their names are in `services`."""

    def __init__(self, services: list[str]) -> None:
        super().__init__(f'not confirmed by: {", ".join(services)}')
        self.services = services


class ServiceSubjects:
    def __init__(self, *, http: httpx.AsyncClient | None = None, tokens: JWTService | None = None) -> None:
        self._http = http
        self._tokens = tokens or JWTService()

    async def erase(self, user_id: str, *, org_id: str) -> list[str]:
        """Every service told; their names. `ErasureIncomplete` if any did not confirm."""
        services = await RegisteredClientDB().approved_services()
        outcomes = await asyncio.gather(*(self._erase_at(service, user_id, org_id) for service in services))
        failed = [service.name for service, ok in zip(services, outcomes) if not ok]
        if failed:
            raise ErasureIncomplete(failed)
        return [service.name for service in services]

    async def export(self, user_id: str, *, org_id: str) -> ExportAnswers:
        services = await RegisteredClientDB().approved_services()
        found = await asyncio.gather(*(self._export_at(service, user_id, org_id) for service in services))
        answers = ExportAnswers()
        for service, export in zip(services, found):
            if export is None:
                answers.missing.append(service.name)
            else:
                answers.sections[service.name] = {section.name: section.rows for section in export.sections}
        return answers

    def _headers(self, service: RegisteredClient, org_id: str) -> dict[str, str]:
        token = self._tokens.sign(
            subject=self._tokens.issuer,
            audience=service.base_url,
            use=TokenUse.IGNI,
            lifetime=CALL_TOKEN_LIFETIME,
            claims={'org': org_id},
        )
        return {'Authorization': f'Bearer {token}'}

    def _client(self) -> httpx.AsyncClient:
        return self._http or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def _erase_at(self, service: RegisteredClient, user_id: str, org_id: str) -> bool:
        client = self._client()
        try:
            response = await client.delete(
                f'{service.base_url}/v1/igni/subjects/{user_id}', headers=self._headers(service, org_id)
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error('Erasure not confirmed by %s (%s)', service.name, type(exc).__name__)
            return False
        finally:
            if self._http is None:
                await client.aclose()
        return True

    async def _export_at(self, service: RegisteredClient, user_id: str, org_id: str) -> ServiceExport | None:
        client = self._client()
        try:
            response = await client.get(
                f'{service.base_url}/v1/igni/subjects/{user_id}/export', headers=self._headers(service, org_id)
            )
            response.raise_for_status()
            return ServiceExport.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning('Subject access: no sections from %s (%s)', service.name, type(exc).__name__)
            return None
        finally:
            if self._http is None:
                await client.aclose()
