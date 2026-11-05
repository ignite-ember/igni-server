"""The journey's own registered service: the smallest one igni's SDK makes.

Step 7 needs a service that registers itself, ships an extension, and
answers igni's export and erasure calls. The extension starter's Team
Notes is the full example of one, but it lives in a repository CI cannot
check out, so in CI those steps skipped. This is the same shape, built
with `igni-service` from the igni checkout the journey already uses, and
nothing else: what it holds is the people igni asked it about.

Run by `journey.sh` in a virtualenv of its own (igni-service, uvicorn,
aiosqlite), never with the server's Python:

    python -m uvicorn --app-dir tools/e2e_journey/service_fixture --factory service:create_app

Settings are igni-service's own environment variables (`IGNI_SERVER_URL`,
`PUBLIC_BASE_URL`, `SERVICE_NAME`, `IGNI_KEY_ENCRYPTION_KEY`,
`IGNI_KEY_DATABASE_URL`, `IGNI_EXTENSION_BUNDLE`).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from igni_service import ExportSection, IgniService, IgniSettings, Subject

logger = logging.getLogger('journey_service')


class JourneyService:
    """Registers, ships its extension, and answers export and erasure."""

    def __init__(self, igni: IgniService) -> None:
        self.igni = igni
        #: `(org_id, user_id)` of everyone igni has asked to export, kept to
        #: give the export something to say.
        self.exported: list[tuple[str, str]] = []
        igni.on_export(self.export)
        igni.on_erase(self.erase)

    async def export(self, subject: Subject) -> list[ExportSection]:
        self.exported.append((subject.org_id, subject.user_id))
        logger.info('journey service: export for %s in %s', subject.user_id, subject.org_id)
        return [ExportSection(name='Journey service', rows=[{'user_id': subject.user_id, 'held': 'nothing'}])]

    async def erase(self, subject: Subject) -> None:
        """Idempotent, as igni requires: nothing is held, so nothing is left."""
        self.exported = [entry for entry in self.exported if entry != (subject.org_id, subject.user_id)]
        logger.info('journey service: erased %s in %s', subject.user_id, subject.org_id)

    def app(self) -> FastAPI:
        app = FastAPI(title='Journey service', lifespan=self.igni.lifespan)
        self.igni.install(app)
        return app


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    return JourneyService(IgniService.from_settings(IgniSettings())).app()
