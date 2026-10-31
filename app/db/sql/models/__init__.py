from pathlib import Path
import pkgutil

from sqlalchemy.orm import declarative_base

#: The shared plane: what has to know about every organisation — accounts,
#: organisations and their domains, memberships, deployment settings, and
#: the routing tables that say which organisation something belongs to.
Base = declarative_base()

#: One organisation's own data, in that organisation's own database
#: (`app.db.sql.tenancy`). A session on one tenant cannot see another's
#: tables at all, so a query that forgot to filter by organisation has
#: nothing to leak. Models on this base never reference the shared plane
#: by foreign key: the two are different databases.
TenantBase = declarative_base()


def load_all_models() -> None:
    """Load all models from this folder."""
    package_dir = Path(__file__).resolve().parent

    modules = pkgutil.walk_packages(path=[str(package_dir)], prefix='app.db.sql.models.')
    for module in modules:
        __import__(module.name)
