"""The encryption key can be rotated. DP-2.

`SECRET_ENCRYPTION_KEY` protects what the deployment stores encrypted:
its token-signing private key and GPU nodes' API keys (F150), and VM
credentials awaiting pickup in each organisation's own database. ISO 27001 A 5.33 asks that such a key can be
rotated (DP-2, F84).

Verified against a live database before these tests were written: a
secret stored under key A, rotated with A as previous and B as primary,
then read back — decrypts under B alone, no longer decrypts under A, and
decrypts under both while both are configured.

The tests below cover the parts that are easy to get subtly wrong:

* **which key encrypts.** `MultiFernet` decrypts with any key it holds
  and encrypts with the *first*. Ordered the other way, a rotation would
  quietly keep writing under the old key;
* **the coverage list.** The tool walks a hardcoded list of columns, so a
  new encrypted column added later would be skipped in silence. Compared
  with the models' markers, every caller of `encrypt_secret`, the live
  schemas and the stored values here;
* **the pass itself.** Round trip, idempotence, an interrupted run
  resumed, a row the live server changes mid-pass, detached
  organisations, and an unreadable row;
* **the half-finished state.** While a previous key is configured it is
  still a working key for this deployment, which is most of what
  rotating was for. Removal is the step people skip.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import uuid

from cryptography.fernet import Fernet, InvalidToken
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.sql.models import Base, load_all_models, TenantBase
from app.db.sql.models.vm_credential import VmCredential
from app.db.sql.tenant_registry import TenantRegistryDB
from app.security.startup_checks import SettingsChecks
from app.services.secrets import decrypt_secret, encrypt_secret, previous_keys, SecretCipherUnavailable
from app.services.secrets.cipher import SecretDecryptError
from tests.conftest import put_in_org, TEST_DB_URL_SYNC

_ROOT = pathlib.Path(__file__).resolve().parent.parent

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
KEY_C = Fernet.generate_key().decode()

#: Each module that calls `encrypt_secret`, and the column it writes.
_WRITES_CIPHERTEXT = {
    'app/db/sql/signing_keys.py': ('signing_keys', 'encrypted_private_key'),
    'app/db/sql/inference_nodes.py': ('inference_nodes', 'api_key'),
    'app/services/cloud/vm_credentials.py': ('vm_credentials', 'pending_pickup'),
}

#: Modules that call `encrypt_secret` for a value held in Redis, not in a
#: column. The rotation tool does not walk Redis and need not: everything
#: sealed there expires within its TTL (F151; `app/db/redis/sealed.py`).
_SEALS_REDIS_VALUES = {'app/db/redis/sealed.py'}


@pytest.fixture
def keys(monkeypatch):
    """Set the primary and previous keys for one test."""

    def configure(primary: str, previous: str = ''):
        monkeypatch.setattr(settings, 'secret_encryption_key', primary)
        monkeypatch.setattr(settings, 'secret_encryption_keys_previous', previous)

    return configure


class TestBothKeysWorkDuringARotation:
    def test_a_secret_written_under_the_old_key_still_reads(self, keys):
        """The whole mechanism. Without this, rotating the key means
        every stored credential has to be supplied again."""
        keys(KEY_A)
        ciphertext = encrypt_secret('gitlab-group-token')

        keys(KEY_B, KEY_A)
        assert decrypt_secret(ciphertext) == 'gitlab-group-token'

    def test_it_stops_reading_once_the_old_key_is_removed(self, keys):
        """Which is the point of removing it, and why the re-encrypt pass
        has to run first."""
        keys(KEY_A)
        ciphertext = encrypt_secret('gitlab-group-token')

        keys(KEY_B)
        with pytest.raises(SecretDecryptError):
            decrypt_secret(ciphertext)

    def test_new_writes_use_the_primary_key(self, keys):
        """`MultiFernet` encrypts with the first key it holds. Ordered
        the other way round, a rotation would keep writing under the old
        key and never finish."""
        keys(KEY_B, KEY_A)
        ciphertext = encrypt_secret('written-during-the-rotation')

        # Readable with the new key alone — so it was not written under
        # the old one.
        keys(KEY_B)
        assert decrypt_secret(ciphertext) == 'written-during-the-rotation'

    def test_several_superseded_keys_are_accepted(self, keys):
        """Two rotations without finishing the first is a real state to
        be in, and the alternative is telling somebody their credentials
        are unrecoverable."""
        keys(KEY_A)
        oldest = encrypt_secret('from-the-first-key')
        keys(KEY_B)
        middle = encrypt_secret('from-the-second-key')

        keys(KEY_C, f'{KEY_B},{KEY_A}')
        assert decrypt_secret(oldest) == 'from-the-first-key'
        assert decrypt_secret(middle) == 'from-the-second-key'

    def test_an_unrelated_key_does_not_read_it(self, keys):
        keys(KEY_A)
        ciphertext = encrypt_secret('secret')

        keys(KEY_C, KEY_B)
        with pytest.raises(SecretDecryptError):
            decrypt_secret(ciphertext)

    def test_the_error_says_what_to_do(self, keys):
        """A decrypt failure mid-rotation is an operator's problem, and
        "invalid token" tells them nothing."""
        keys(KEY_A)
        ciphertext = encrypt_secret('secret')
        keys(KEY_B)

        with pytest.raises(SecretDecryptError, match='SECRET_ENCRYPTION_KEYS_PREVIOUS'):
            decrypt_secret(ciphertext)


class TestParsingTheListOfOldKeys:
    """Comma-separated, because a person types it into an env file or a
    Kubernetes Secret halfway through a rotation."""

    def test_one_key(self, keys):
        keys(KEY_B, KEY_A)
        assert previous_keys() == [KEY_A]

    def test_several(self, keys):
        keys(KEY_C, f'{KEY_A},{KEY_B}')
        assert previous_keys() == [KEY_A, KEY_B]

    def test_whitespace_and_a_trailing_comma_are_tolerated(self, keys):
        """The obvious typo. Refusing to start over it would be theatre;
        the keys are still unambiguous."""
        keys(KEY_C, f' {KEY_A} , {KEY_B} , ')
        assert previous_keys() == [KEY_A, KEY_B]

    def test_unset_means_none(self, keys):
        keys(KEY_A)
        assert previous_keys() == []

    def test_a_malformed_old_key_names_its_position(self, keys):
        """The value is a comma-separated list, so "one of your old keys
        is malformed" is not actionable."""
        keys(KEY_C, f'{KEY_A},not-a-key')

        with pytest.raises(SecretCipherUnavailable, match='entry 2'):
            decrypt_secret(encrypt_secret('x'))

    def test_a_missing_primary_key_is_reported_as_such(self, keys):
        """Distinct from a decrypt failure: one is "this server is not
        configured", the other is "your secret is corrupt"."""
        keys('', KEY_A)

        with pytest.raises(SecretCipherUnavailable, match='not configured'):
            encrypt_secret('x')


class _Killed(Exception):
    """Stands in for the process dying partway through a pass."""


def _shared_url() -> str:
    return TEST_DB_URL_SYNC


def _tenant_url(database: str) -> str:
    base, _, _ = TEST_DB_URL_SYNC.rpartition('/')
    return f'{base}/{database}'


def _query(url: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            return list(conn.execute(text(sql), params or {}).fetchall())
    finally:
        engine.dispose()


def _tenant_databases() -> list[str]:
    return [str(row.database) for row in _query(_shared_url(), 'SELECT database FROM tenants ORDER BY number')]


def _store_signing_key(kid: str, plaintext: str = 'private-pem') -> None:
    """A signing-key row encrypted under whatever key is configured now."""
    _query(
        _shared_url(),
        'INSERT INTO signing_keys (kid, algorithm, encrypted_private_key, public_jwk, activates_at, created_at) '
        "VALUES (:kid, 'RS256', :value, '{}'::jsonb, now() + interval '1 year', now()) RETURNING kid",
        {'kid': kid, 'value': encrypt_secret(plaintext)},
    )


def _store_node_key(node_id: str, plaintext: str = 'node-proxy-key') -> None:
    """A portal-added GPU node whose API key is encrypted under the key configured now."""
    _query(
        _shared_url(),
        'INSERT INTO inference_nodes (id, node_id, base_url, models, api_key, auth) '
        "VALUES (:id, :node_id, 'http://gpu.internal:8000', '[\"m\"]'::jsonb, :value, 'api_key') RETURNING id",
        {'id': str(uuid.uuid4()), 'node_id': node_id, 'value': encrypt_secret(plaintext)},
    )


async def _store_pickup(org_id: str, producer_id: str, plaintext: str = 'igvm1.credential') -> str:
    """A VM credential awaiting pickup, in the organisation's own database."""
    row = VmCredential(
        id=uuid.uuid4().hex,
        producer_kind='app',
        producer_id=producer_id,
        secret_hash='h',
        previous_hashes=[],
        pending_pickup=encrypt_secret(plaintext),
        created_by_kind='person',
        created_by_id='someone',
    )
    await put_in_org(org_id, row)
    return row.id


def _stored_ciphertexts() -> list[str]:
    """Every value in every listed column, shared plane and organisations."""
    values = [str(r.v) for r in _query(_shared_url(), 'SELECT encrypted_private_key AS v FROM signing_keys')]
    values += [
        str(r.v) for r in _query(_shared_url(), 'SELECT api_key AS v FROM inference_nodes WHERE api_key IS NOT NULL')
    ]
    for database in _tenant_databases():
        values += [
            str(r.v)
            for r in _query(
                _tenant_url(database), 'SELECT pending_pickup AS v FROM vm_credentials WHERE pending_pickup IS NOT NULL'
            )
        ]
    return values


def _readable_under(primary: str, ciphertexts: list[str]) -> int:
    count = 0
    for value in ciphertexts:
        try:
            Fernet(primary.encode()).decrypt(value.encode())
            count += 1
        except InvalidToken:
            pass
    return count


@pytest.fixture
async def stored_under_a(keys):
    """One signing key and one GPU node's key in the shared plane and three
    pickups in two organisations, all written under KEY_A; then KEY_B made
    current with KEY_A previous — the state step 2 of the runbook leaves."""
    keys(KEY_A)
    _store_signing_key('kid-1')
    _store_node_key('gpu-1')
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    await _store_pickup(first, 'app-1')
    await _store_pickup(first, 'app-2')
    await _store_pickup(second, 'app-3')
    keys(KEY_B, KEY_A)
    return first, second


class TestTheToolCoversEveryEncryptedColumn:
    """The list is hardcoded, so it can go stale. Checked four ways,
    because a new encrypted column that the rotation tool skips is a
    credential that silently stays on the old key — and becomes
    unreadable the day that key is removed. The first version of this
    check read only the shared plane's `encrypted%` columns, and missed
    `vm_credentials.pending_pickup` in every organisation's database."""

    def test_the_models_mark_exactly_the_listed_columns(self):
        from tools.rotate_secret_key import SHARED_COLUMNS, TENANT_COLUMNS

        load_all_models()

        def marked(metadata) -> set[tuple[str, str]]:
            return {
                (table.name, column.name)
                for table in metadata.tables.values()
                for column in table.columns
                if column.info.get('encrypted')
            }

        assert marked(Base.metadata) == {(t, c) for t, _pk, c in SHARED_COLUMNS}, (
            "a shared-plane column is marked info={'encrypted': True} and not in SHARED_COLUMNS, or the other way round"
        )
        assert marked(TenantBase.metadata) == {(t, c) for t, _pk, c in TENANT_COLUMNS}, (
            "an organisation column is marked info={'encrypted': True} and not in TENANT_COLUMNS, "
            'or the other way round'
        )

    def test_every_caller_of_encrypt_secret_is_accounted_for(self):
        """A new caller is a new ciphertext somewhere. Until somebody says
        where — marks the column and lists it in the tool — this fails."""
        from tools.rotate_secret_key import ENCRYPTED_COLUMNS

        callers: set[str] = set()
        fernet_users: set[str] = set()
        for path in (_ROOT / 'app').rglob('*.py'):
            relative = str(path.relative_to(_ROOT))
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', '')
                if name == 'encrypt_secret':
                    callers.add(relative)
                if name in ('Fernet', 'MultiFernet'):
                    fernet_users.add(relative)

        accounted = set(_WRITES_CIPHERTEXT) | _SEALS_REDIS_VALUES
        assert callers == accounted, (
            f'encrypt_secret is called from {sorted(callers - accounted)} too. Mark the column it '
            "writes info={'encrypted': True}, add it to tools/rotate_secret_key.py, and record it here — "
            'or, for a value in Redis with a TTL, go through app/db/redis/sealed.py.'
        )
        listed = {(t, c) for t, _pk, c in ENCRYPTED_COLUMNS}
        for path, column in _WRITES_CIPHERTEXT.items():
            assert column in listed, f'{path} writes {column}, which the rotation tool does not walk'

        # Ciphertext made some other way would bypass the key list entirely.
        assert fernet_users <= {'app/services/secrets/cipher.py', 'app/security/secret_checks.py'}, fernet_users

    async def test_every_listed_column_exists_and_every_encrypted_name_is_listed(self):
        """Both directions, against the live schemas — the shared plane and
        an organisation's own database. A stale entry makes the tool fail
        mid-rotation, the worst moment to debug it."""
        from tools.rotate_secret_key import SHARED_COLUMNS, TENANT_COLUMNS

        await TenantRegistryDB().database_of(str(uuid.uuid4()))
        (tenant,) = _tenant_databases()

        for url, columns in ((_shared_url(), SHARED_COLUMNS), (_tenant_url(tenant), TENANT_COLUMNS)):
            real = {
                (r.table_name, r.column_name)
                for r in _query(
                    url,
                    "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'",
                )
            }
            for table, pk, column in columns:
                assert (table, column) in real, f'{table}.{column} does not exist'
                assert (table, pk) in real, f'{table}.{pk} does not exist'
            named = {(t, c) for t, c in real if c.startswith('encrypted')}
            assert named <= {(t, c) for t, _pk, c in columns}, f'not in the rotation tool: {named}'

    async def test_no_ciphertext_is_stored_anywhere_the_tool_does_not_look(self, stored_under_a):
        """What is actually in the databases, not what the code says. Every
        text column of every table, shared and per organisation, scanned
        for a Fernet token (they all begin `gAAAAA`)."""
        from tools.rotate_secret_key import ENCRYPTED_COLUMNS

        listed = {(t, c) for t, _pk, c in ENCRYPTED_COLUMNS}
        found: set[tuple[str, str]] = set()
        for url in [_shared_url(), *(_tenant_url(d) for d in _tenant_databases())]:
            columns = _query(
                url,
                'SELECT table_name, column_name FROM information_schema.columns '
                "WHERE table_schema = 'public' AND data_type IN ('text', 'character varying')",
            )
            for column in columns:
                hit = _query(
                    url,
                    f'SELECT 1 FROM "{column.table_name}" WHERE "{column.column_name}" LIKE :token LIMIT 1',
                    {'token': 'gAAAAA%'},
                )
                if hit:
                    found.add((column.table_name, column.column_name))

        assert found, 'the fixture stored nothing, so this proves nothing'
        assert found <= listed, f'ciphertext stored outside the rotation tool: {sorted(found - listed)}'


#: Columns whose name says "secret" and which hold none, with the reason.
#: Anything else so named must be encrypted (marked and listed) or hold a
#: one-way hash. F150 was a credential column nobody had classified.
_SECRET_NAMED_BUT_NOT_A_SECRET = {
    # bcrypt: what is stored cannot be used to sign in.
    ('users', 'hashed_password'): 'a bcrypt hash',
    # SHA-256 of the credential and of its superseded generations.
    ('vm_credentials', 'secret_hash'): 'a hash',
    ('vm_credentials', 'previous_hashes'): 'hashes',
    # SHA-256 of a personal API key's 256-bit secret; the key is shown once.
    ('api_keys', 'secret_hash'): 'a hash',
    # Never written by anything (`test_invite_cannot_be_guessed.py`).
    ('org_invitations', 'token'): 'a dead column',
    # Never written by anything: a pending address is confirmed with a
    # one-time code held in Redis for minutes, not a stored token.
    ('user_roles', 'email_verification_token'): 'a dead column',
    # A timestamp, not a token.
    ('users', 'tokens_valid_after'): 'a timestamp',
    # Counts of model tokens.
    ('chat_usage', 'input_tokens'): 'a count',
    ('chat_usage', 'output_tokens'): 'a count',
    ('chat_usage', 'total_tokens'): 'a count',
}

_SECRET_NAME = re.compile(r'token|secret|passw|api_?key|private|credential|hash')


class TestEveryCredentialColumnIsEncrypted:
    """Not only that the tool walks every encrypted column, but that every
    column holding a credential is one. F150 found `inference_nodes.api_key`
    in plaintext beside the encrypted signing key."""

    def test_each_secret_named_column_is_encrypted_hashed_or_explained(self):
        load_all_models()
        unexplained = sorted(
            (table.name, column.name)
            for metadata in (Base.metadata, TenantBase.metadata)
            for table in metadata.tables.values()
            for column in table.columns
            if _SECRET_NAME.search(column.name)
            and not column.info.get('encrypted')
            and (table.name, column.name) not in _SECRET_NAMED_BUT_NOT_A_SECRET
        )
        assert not unexplained, (
            f"{unexplained}: encrypt it (encrypt_secret, info={{'encrypted': True}}, tools/rotate_secret_key.py) "
            'or say here why it holds no secret'
        )

    def test_the_dead_ones_are_still_dead(self):
        """A column explained away as never written must stay never written."""
        dead = [column for (_t, column), why in _SECRET_NAMED_BUT_NOT_A_SECRET.items() if why == 'a dead column']
        for path in (_ROOT / 'app').rglob('*.py'):
            relative = str(path.relative_to(_ROOT))
            if relative.startswith(('app/db/sql/models/', 'app/services/subject_access/')):
                continue
            text_ = path.read_text()
            for column in dead:
                if column == 'token':
                    continue  # too common a word; `test_invite_cannot_be_guessed.py` guards the table
                assert column not in text_, f'{relative} uses {column}, which this file says nothing writes'


class TestTheReEncryptPass:
    """`tools/rotate_secret_key.py` itself, against real databases."""

    async def test_a_dry_run_reports_and_writes_nothing(self, stored_under_a):
        from tools.rotate_secret_key import rotate

        before = _stored_ciphertexts()
        counts = rotate(apply=False)

        assert counts['databases'] == 3
        assert counts['checked'] == 5
        assert counts['rotated'] == 5
        assert _stored_ciphertexts() == before

    async def test_the_round_trip(self, stored_under_a, keys):
        """Written under A, rotated to B, read under B alone — and no
        longer under A alone, which is what proves it was re-encrypted
        rather than merely still readable."""
        from tools.rotate_secret_key import rotate

        counts = rotate(apply=True)
        assert counts == {
            'databases': 3,
            'checked': 5,
            'already_current': 0,
            'rotated': 5,
            'unreadable': 0,
            'changed_underneath': 0,
            'missing': 0,
        }

        stored = _stored_ciphertexts()
        assert _readable_under(KEY_B, stored) == 5
        assert _readable_under(KEY_A, stored) == 0

        keys(KEY_B)
        assert sorted(decrypt_secret(v) for v in stored) == ['igvm1.credential'] * 3 + ['node-proxy-key', 'private-pem']

    async def test_it_is_idempotent(self, stored_under_a):
        from tools.rotate_secret_key import rotate

        rotate(apply=True)
        once = _stored_ciphertexts()
        again = rotate(apply=True)

        assert again['already_current'] == 5
        assert again['rotated'] == 0
        # Nothing rewritten: a second pass is a read.
        assert _stored_ciphertexts() == once

    async def test_an_interrupted_pass_resumes(self, stored_under_a, monkeypatch, keys):
        """Killed partway: what it finished is on B, the rest still reads
        under A, which is still configured. Running it again finishes."""
        import tools.rotate_secret_key as tool

        real = tool.encrypt_secret
        calls = {'n': 0}

        def dies_on_the_third(plaintext: str) -> str:
            calls['n'] += 1
            if calls['n'] == 3:
                raise _Killed
            return real(plaintext)

        monkeypatch.setattr(tool, 'encrypt_secret', dies_on_the_third)
        with pytest.raises(_Killed):
            tool.rotate(apply=True)

        stored = _stored_ciphertexts()
        assert _readable_under(KEY_B, stored) == 2
        assert _readable_under(KEY_A, stored) == 3
        assert all(decrypt_secret(v) for v in stored), 'everything still reads mid-rotation'

        monkeypatch.setattr(tool, 'encrypt_secret', real)
        resumed = tool.rotate(apply=True)
        assert resumed['already_current'] == 2
        assert resumed['rotated'] == 3

        keys(KEY_B)
        assert _readable_under(KEY_B, _stored_ciphertexts()) == 5

    async def test_a_row_changed_while_it_runs_is_not_written_back(self, keys, monkeypatch):
        """The server is live during a rotation. A node agent picking up a
        credential clears `pending_pickup` between the tool's read and its
        write; writing back unconditionally would put a picked-up
        credential up for pickup again."""
        import tools.rotate_secret_key as tool

        keys(KEY_A)
        org = str(uuid.uuid4())
        row_id = await _store_pickup(org, 'app-1')
        keys(KEY_B, KEY_A)
        (database,) = _tenant_databases()

        real = tool.encrypt_secret

        def picked_up_meanwhile(plaintext: str) -> str:
            _query(
                _tenant_url(database),
                'UPDATE vm_credentials SET pending_pickup = NULL WHERE id = :id RETURNING id',
                {'id': row_id},
            )
            return real(plaintext)

        monkeypatch.setattr(tool, 'encrypt_secret', picked_up_meanwhile)
        counts = tool.rotate(apply=True)

        assert counts['changed_underneath'] == 1
        assert counts['rotated'] == 0
        (row,) = _query(_tenant_url(database), 'SELECT pending_pickup FROM vm_credentials')
        assert row.pending_pickup is None

    async def test_a_detached_organisation_is_rotated_too(self, keys):
        """A deleted organisation's database is kept. Left on the old key,
        it becomes unreadable the day that key is removed."""
        from tools.rotate_secret_key import rotate

        keys(KEY_A)
        org = str(uuid.uuid4())
        await _store_pickup(org, 'app-1')
        _query(_shared_url(), "UPDATE tenants SET state = 'detached' WHERE org_id = :o RETURNING org_id", {'o': org})
        keys(KEY_B, KEY_A)

        assert rotate(apply=True)['rotated'] == 1
        assert _readable_under(KEY_B, _stored_ciphertexts()) == 1

    async def test_a_registered_database_that_does_not_exist_is_counted_and_skipped(self, keys, capsys):
        """A registry row whose database is gone — dropped by hand, or a
        restore that brought back the shared plane alone. The pass goes on
        with the others and counts it, so the operator sees it."""
        from tools.rotate_secret_key import rotate

        keys(KEY_A)
        org = str(uuid.uuid4())
        await _store_pickup(org, 'app-1')
        _query(
            _shared_url(),
            'INSERT INTO tenants (org_id, number, database, state, created_at) '
            "VALUES ('org-gone', 99, 'ember_test_t99', 'active', now()) RETURNING org_id",
        )
        keys(KEY_B, KEY_A)

        counts = rotate(apply=True)

        assert (counts['databases'], counts['missing'], counts['rotated']) == (2, 1, 1)
        assert (
            '  organisation org-gone (active): database ember_test_t99 is registered but does not exist; skipped'
            in capsys.readouterr().out.splitlines()
        )

    async def test_an_unreadable_row_fails_loudly_and_is_left_alone(self, keys, monkeypatch, capsys):
        """A row no configured key opens is a lost credential. The tool
        exits non-zero and says not to remove the old key, and does not
        touch the row."""
        import tools.rotate_secret_key as tool

        keys(KEY_C)
        _store_signing_key('kid-orphan')
        keys(KEY_B, KEY_A)
        before = _stored_ciphertexts()

        monkeypatch.setattr('sys.argv', ['rotate_secret_key.py', '--apply'])
        assert tool.main() == 1

        err = capsys.readouterr().err
        assert 'UNREADABLE' in err
        assert 'Do not remove' in err
        assert _stored_ciphertexts() == before

    async def test_the_command_reports_counts(self, stored_under_a, monkeypatch, capsys):
        import tools.rotate_secret_key as tool

        monkeypatch.setattr('sys.argv', ['rotate_secret_key.py', '--apply'])
        assert tool.main() == 0
        out = capsys.readouterr().out
        assert re.search(r'checked\s+5', out), out
        assert re.search(r'rotated\s+5', out), out
        assert 'remove SECRET_ENCRYPTION_KEYS_PREVIOUS' in out


class TestTheStartupChecksNagUntilItIsFinished:
    def test_it_says_so_while_a_previous_key_is_configured(self, monkeypatch):
        """Every boot, not once: removal is the step people skip, and
        until they do it the superseded key still opens everything."""
        monkeypatch.setattr(settings, 'app_env', 'prod')
        monkeypatch.setattr(settings, 'secret_encryption_key', KEY_B)
        monkeypatch.setattr(settings, 'secret_encryption_keys_previous', KEY_A)

        problems = SettingsChecks().check(raise_on_unsafe=False)

        assert any('SECRET_ENCRYPTION_KEYS_PREVIOUS holds 1 key' in p for p in problems), problems
        assert any('rotate_secret_key' in p for p in problems), problems

    def test_it_counts_them(self, monkeypatch):
        monkeypatch.setattr(settings, 'app_env', 'prod')
        monkeypatch.setattr(settings, 'secret_encryption_key', KEY_C)
        monkeypatch.setattr(settings, 'secret_encryption_keys_previous', f'{KEY_A},{KEY_B}')

        problems = SettingsChecks().check(raise_on_unsafe=False)

        assert any('holds 2 key' in p for p in problems), problems

    def test_a_malformed_old_key_is_caught_at_boot(self, monkeypatch):
        """Rather than on the first credential read, which may be days
        later — a truncated paste is the likely cause."""
        monkeypatch.setattr(settings, 'app_env', 'prod')
        monkeypatch.setattr(settings, 'secret_encryption_key', KEY_B)
        monkeypatch.setattr(settings, 'secret_encryption_keys_previous', 'truncated-pas')

        problems = SettingsChecks().check(raise_on_unsafe=False)

        assert any('not a valid Fernet key' in p for p in problems), problems

    def test_a_finished_rotation_is_silent(self, monkeypatch):
        """A deployment that completed one, or never started, must not be
        nagged — a warning that is always on is a warning nobody reads."""
        monkeypatch.setattr(settings, 'app_env', 'prod')
        monkeypatch.setattr(settings, 'secret_encryption_key', KEY_B)
        monkeypatch.setattr(settings, 'secret_encryption_keys_previous', '')

        problems = SettingsChecks().check(raise_on_unsafe=False)

        assert not any('SECRET_ENCRYPTION_KEYS_PREVIOUS' in p for p in problems), problems


class TestTheToolIsActuallyShipped:
    def test_it_is_not_in_a_gitignored_directory(self):
        """It was written in `scripts/`, which `.gitignore` excludes as
        `/scripts/*` — so it would have existed on one laptop and in no
        release. For the tool that finishes a key rotation that is worse
        than not having one, because the procedure references it.
        """
        result = subprocess.run(
            ['git', 'check-ignore', '-q', 'tools/rotate_secret_key.py'],
            cwd=_ROOT,
            capture_output=True,
        )

        # `git check-ignore -q` exits 0 when the path IS ignored.
        assert result.returncode != 0, 'tools/rotate_secret_key.py is gitignored and will not ship'

    def test_it_is_tracked(self):
        result = subprocess.run(
            ['git', 'ls-files', '--error-unmatch', 'tools/rotate_secret_key.py'],
            cwd=_ROOT,
            capture_output=True,
        )

        assert result.returncode == 0, 'tools/rotate_secret_key.py is not tracked by git'

    def test_every_document_that_names_it_names_the_same_path(self):
        """The procedure is quoted in three places. A stale invocation in
        one of them is read at the worst moment."""
        for path in (
            'app/services/secrets/cipher.py',
            'app/security/secret_checks.py',
            'docs/COMPLIANCE_TRACKER.md',
        ):
            text_ = (_ROOT / path).read_text()
            if 'rotate_secret_key' in text_:
                assert 'scripts.rotate_secret_key' not in text_, f'{path} names the old location'
                assert 'tools/rotate_secret_key.py' in text_, f'{path} does not give the runnable path'
