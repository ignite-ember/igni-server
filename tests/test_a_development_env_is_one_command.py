"""`make dev-env` writes a `.env` that `make migrate` and `make run` start on.

Found on a fresh clone, following the Quick start as written ("copy dist.env
to .env and set at minimum SECRET_ENCRYPTION_KEY"): `make migrate` tried a
Unix socket (POSTGRES_HOST blank), then a database named after the user
(POSTGRES_DB blank is an empty name, not "unset"), and `make run` refused to
start (APP_ENV=prod, PUBLIC_BASE_URL blank). With those fixed, nobody could
sign in: no relay, and LOG_SIGN_IN_CODES off.
"""

from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values
import pytest

from app.config import Settings, settings
from app.security.startup_checks import SettingsChecks
import tools.dev_env as dev_env

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def path(tmp_path: Path) -> Path:
    out = tmp_path / '.env'
    assert dev_env.main(['--out', str(out), '--pg-port', '56701', '--redis-port', '57701']) == 0
    return out


@pytest.fixture
def written(path: Path) -> dict[str, str | None]:
    return dotenv_values(path)


def test_it_is_dist_env_with_the_development_values(written):
    template = dotenv_values(_ROOT / 'dist.env')
    assert set(template) <= set(written), 'a setting of the template went missing'
    assert written['APP_ENV'] == 'dev'
    assert (written['POSTGRES_HOST'], written['POSTGRES_PORT'], written['POSTGRES_DB']) == (
        'localhost',
        '56701',
        'igni',
    )
    assert (written['REDIS_HOST'], written['REDIS_PORT']) == ('localhost', '57701')
    assert written['PUBLIC_BASE_URL'] == 'http://localhost:7777'
    assert written['PORTAL_BASE_URL'] == 'http://localhost:3000'
    assert written['LOG_SIGN_IN_CODES'] == 'true'
    Fernet(written['SECRET_ENCRYPTION_KEY'].encode())  # a real key, not a placeholder


def test_each_file_gets_a_key_of_its_own(tmp_path: Path):
    keys = set()
    for name in ('a', 'b'):
        out = tmp_path / name
        dev_env.main(['--out', str(out)])
        keys.add(dotenv_values(out)['SECRET_ENCRYPTION_KEY'])
    assert len(keys) == 2


def test_the_server_starts_on_it(path, monkeypatch):
    """The startup checks raise nothing — what `make run` refused before."""
    for name in ('APP_ENV', 'SECRET_ENCRYPTION_KEY', 'PUBLIC_BASE_URL', 'LOG_SIGN_IN_CODES'):
        monkeypatch.delenv(name, raising=False)  # the file's values, not the suite's
    loaded = Settings(_env_file=path)  # type: ignore[call-arg]
    for field in ('app_env', 'secret_encryption_key', 'public_base_url', 'log_sign_in_codes'):
        monkeypatch.setattr(settings, field, getattr(loaded, field))

    SettingsChecks().check()  # raises UnsafeDeployment on a fatal problem


def test_it_will_not_overwrite_a_file(tmp_path: Path, capsys):
    out = tmp_path / '.env'
    out.write_text('SECRET_ENCRYPTION_KEY=mine\n')

    assert dev_env.main(['--out', str(out)]) == 1
    assert out.read_text() == 'SECRET_ENCRYPTION_KEY=mine\n'
    assert 'not overwriting' in capsys.readouterr().err


def test_dist_env_names_the_database_it_says_is_the_default():
    """`POSTGRES_DB=` connected to a database called whatever POSTGRES_USER is."""
    assert dotenv_values(_ROOT / 'dist.env')['POSTGRES_DB'] == 'igni'
