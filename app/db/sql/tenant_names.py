"""What each organisation's database is called.

`<db>_t<number>` for an organisation, `<db>_tenant_template` for the
template every one is copied from, where `<db>` is the shared plane's
database. `containers/backup.sh`, the API image's Dockerfile and the
test suite all look for databases by these names, so the convention is
written down once, here.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url, URL

from app.config import settings


class TenantNames:
    """Names and URLs of the tenant databases, derived from the shared plane's URL."""

    def __init__(self, *, shared_url: URL | None = None) -> None:
        # None reads the configured URL on every call rather than once, so
        # a test that points the settings elsewhere is followed.
        self._shared_url = shared_url

    def _shared(self) -> URL:
        return self._shared_url if self._shared_url is not None else make_url(settings.postgres_url)

    def base_name(self) -> str:
        """The shared plane's database, which every tenant name starts with."""
        return str(self._shared().database)

    def name(self, number: int) -> str:
        """The database of the organisation with this tenant number."""
        return f'{self.base_name()}_t{number}'

    def template(self) -> str:
        """The database every new organisation's is copied from."""
        return f'{self.base_name()}_tenant_template'

    def sync_url(self, database: str) -> URL:
        """A psycopg2 URL for `database`, for alembic and for DDL."""
        return self._shared().set(drivername='postgresql+psycopg2', database=database)
