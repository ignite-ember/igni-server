"""Refuse to serve a deployment that cannot authenticate anybody.

Every setting here has a safe-looking default that is not safe, which is
the whole reason this file exists. A missing secret is not a
misconfiguration the server can carry on through — it is an open door,
and the failure mode is silent: tokens still verify, requests still
succeed, and nothing in a log says why.

The check runs at **startup**, not at import. ``app/config.py``
constructs ``settings`` at module scope, so raising there would make
importing configuration depend on deployment posture and take every test
with it. "The server must not start" is the actual requirement, and
``lifespan`` is where that belongs.

Rules are graded by ``app_env`` — the same signal ``main.py`` uses to
decide whether to expose ``/docs``. Anything that is not ``dev`` is
treated as serving real users.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet

from app.config import settings
from app.db.sql.deployment_checks import DeploymentChecksDB
from app.security.bypass_identity import BYPASS_ORG_ID, BYPASS_USER_ID
from app.security.super_admin import looks_like_bcrypt
from app.services.email.delivery import EmailDelivery

logger = logging.getLogger(__name__)


class UnsafeDeployment(RuntimeError):
    """Raised at startup when a setting would leave the door open."""


#: The floor for a shared secret presented in a header. There is no
#: attempt limit on the comparison, so length is all there is.
_MIN_SHARED_SECRET_LENGTH = 32


def _is_development() -> bool:
    return settings.app_env.lower() == 'dev'


def _fatal(problem: str, fix: str) -> str:
    return f'{problem}\n  Fix: {fix}'


def check_deployment(*, raise_on_unsafe: bool = True) -> list[str]:
    """Return the problems found; raise on the fatal ones outside dev.

    Returns the list of warnings so a caller can log them. Fatal problems
    raise :class:`UnsafeDeployment` unless *raise_on_unsafe* is False,
    which exists so a test can assert on the message rather than on an
    exception type alone.
    """
    fatal, warnings = _assess()

    for warning in warnings:
        logger.warning('Deployment check: %s', warning)

    if fatal and raise_on_unsafe:
        raise UnsafeDeployment(
            'Refusing to start — this deployment cannot authenticate anybody:\n\n'
            + '\n\n'.join(fatal)
            + '\n\nAPP_ENV=dev downgrades the others to warnings for local work; '
            'SECRET_ENCRYPTION_KEY is required everywhere.'
        )

    return warnings + fatal


def deployment_notices() -> list[str]:
    """The startup warnings, for the portal's super admins (`GET /portal/deployment/notices`).

    A warning in the log reaches whoever reads the log, which on a real
    deployment is often not the person who can act on it — and for
    ``LOG_SIGN_IN_CODES`` (F35) the log is the exposure itself. The same
    checks, worded the same way, are shown to the people who run the
    deployment. Settings are read at startup, so the answer does not
    change while the process runs; it is recomputed rather than cached so
    there is no second copy to fall out of step. Not logged again here.
    """
    _fatal_problems, warnings = _assess()
    return warnings


def _assess() -> tuple[list[str], list[str]]:
    """Every check, as ``(fatal, warnings)``, without logging or raising."""
    fatal: list[str] = []
    warnings: list[str] = []

    # ── The key that protects the signing key ────────────────────────
    #
    # The server signs its tokens with a key pair it generates itself
    # and keeps in Postgres, encrypted under ``SECRET_ENCRYPTION_KEY``
    # (app/services/identity/key_ring.py). Without that key it cannot
    # store a signing key, so it cannot sign anybody in — fatal in every
    # environment, dev included, because there is no insecure fallback
    # to allow: the alternative is a key in plaintext in the database.
    encryption_key = settings.secret_encryption_key
    if not encryption_key:
        fatal.append(
            _fatal(
                'SECRET_ENCRYPTION_KEY is not set. The server keeps its token-signing key '
                'encrypted under it, so without it nobody can sign in.',
                "generate one with `python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'` and set SECRET_ENCRYPTION_KEY.",
            )
        )
    else:
        try:
            Fernet(encryption_key.encode())
        except (ValueError, TypeError):
            fatal.append(
                _fatal(
                    'SECRET_ENCRYPTION_KEY is not a valid Fernet key (32 url-safe base64 bytes), '
                    'so the token-signing key cannot be stored or read.',
                    'check for a truncated paste, or generate a new one.',
                )
            )

    # ── The issuer ───────────────────────────────────────────────────
    #
    # ``PUBLIC_BASE_URL`` is where CodeIndex, Artifacts and every other
    # registered client fetch this server's public keys. Over plain
    # HTTP, anybody on the path can substitute their own keys and mint a
    # token for any person in any organisation — the same reason an
    # ``http://`` OIDC issuer is refused below.
    if not _is_development() and not settings.public_base_url.startswith('https://'):
        fatal.append(
            _fatal(
                f'PUBLIC_BASE_URL is {settings.public_base_url!r}. Services fetch this '
                f"server's signing keys from it, and over plain HTTP those keys can be replaced in transit.",
                "set PUBLIC_BASE_URL to the server's https:// address.",
            )
        )

    # ── The portal auth bypass ───────────────────────────────────────
    #
    # Makes every portal request a hardcoded admin in a demo org. It
    # defaults to False, but it is set in the local compose file and in
    # .env, so it travels easily to somewhere it must not be.
    if settings.portal_auth_bypass and not _is_development():
        fatal.append(
            _fatal(
                f'PORTAL_AUTH_BYPASS is enabled with APP_ENV={settings.app_env!r}. Every '
                f'portal request would be treated as an administrator with no credentials.',
                'unset PORTAL_AUTH_BYPASS, or set APP_ENV=dev if this really is a development deployment.',
            )
        )

    # ── Where one-time codes go when there is no relay ───────────────
    #
    # With no relay, ``record_undeliverable_code`` can write sign-in and
    # password-reset codes into the application log so an operator can
    # pass them on — never back to the caller, who could otherwise
    # request one for any address and read it straight back.
    #
    # F35: that used to be the *default*. ``email_enabled`` is False and
    # ``smtp_host`` empty out of the box, so a deployment that had not got
    # to email yet was writing working credentials into logs that are
    # shipped to an aggregator, kept for months and readable by more
    # people than should be able to sign in as anybody. It is now an
    # explicit ``LOG_SIGN_IN_CODES=true``, and while it is on this says so
    # on every boot, in every environment — it is a deliberate exposure,
    # and the kind that outlives the first setup it was turned on for.
    relay = EmailDelivery().enabled
    if settings.log_sign_in_codes and not relay:
        warnings.append(
            'LOG_SIGN_IN_CODES is on and no mail relay is configured, so every sign-in, sign-up '
            'and password-reset code is written to the application log. Anyone who can read '
            'these logs can sign in as any user and reset any password — treat them as '
            'credential material, and turn LOG_SIGN_IN_CODES off once a relay is configured.'
        )
    elif settings.log_sign_in_codes:
        warnings.append(
            'LOG_SIGN_IN_CODES is on. A relay is configured, so codes are emailed and it has '
            'no effect today — but if the relay is ever removed, codes start going to the '
            'log without anybody choosing it again. Unset it.'
        )
    elif not relay and not _is_development():
        # Not a refusal: no relay is supported, and a deployment that
        # signs everybody in through SSO needs none. But a code requested
        # here now reaches nobody, and saying so at boot beats a user
        # waiting for an email that is never sent.
        warnings.append(
            'No mail relay is configured (EMAIL_ENABLED / SMTP_HOST, or EMAIL_BACKEND=ses), and '
            'LOG_SIGN_IN_CODES is off, so sign-in and password-reset codes reach nobody. '
            'Configure a relay; or, for first setup only, set LOG_SIGN_IN_CODES=true to have '
            'codes written to the server log — where anyone who can read it can use them.'
        )

    # ── Shared secrets that are set but too short ────────────────────
    #
    # Both of these are compared with ``compare_digest`` and both answer
    # 401 on a mismatch, which is right — but neither has an attempt
    # limit, because neither is a user-facing login. So their whole
    # strength is their length, and nothing checked it:
    # ``NODE_REGISTRATION_TOKEN=test`` authenticated a GPU node
    # registration when tried.
    #
    # Warned, not fatal, and only when *set*: leaving either unset is a
    # supported state that disables the surface it guards — the node API
    # answers 404 rather than advertising a disabled endpoint.
    for name, value, guards in (
        ('NODE_REGISTRATION_TOKEN', settings.node_registration_token, 'GPU node registration'),
    ):
        if value and len(value) < _MIN_SHARED_SECRET_LENGTH:
            warnings.append(
                f'{name} is only {len(value)} characters. It guards {guards}, it is '
                f'compared without any attempt limit, so its length is the whole of '
                f'its strength. Use at least {_MIN_SHARED_SECRET_LENGTH}.'
            )

    # ── The break-glass key ──────────────────────────────────────────
    #
    # A warning rather than fatal: an install with no break-glass key is
    # a supported state (super admins are accounts; the key only reaches
    # deployment-wide settings when nobody can sign in). What is not
    # supported is a value that is set and unusable — and the natural
    # thing to paste into a field named ``..._PASSWORD_HASH`` is a
    # password, which bcrypt rejects with ``Invalid salt`` on every
    # attempt. It fails closed, so this is about diagnosis.
    if settings.super_admin_password_hash:
        if not looks_like_bcrypt(settings.super_admin_password_hash):
            warnings.append(
                'SUPER_ADMIN_PASSWORD_HASH is set but is not a bcrypt hash, so the '
                'break-glass key will never work. It wants the hash, not the password: '
                'python -c "import bcrypt; '
                "print(bcrypt.hashpw(b'YOUR-PASSWORD', bcrypt.gensalt()).decode())\""
            )

    # ── Public URLs still pointing at a developer's laptop ───────────
    #
    # These default to localhost and are absent from ``dist.env``, so a
    # deployment that follows the template inherits them. They are not a
    # security hole — they break in the open — but they break in places
    # nobody is watching: an invite email carrying a link to
    # ``localhost:3000`` sends fine and is useless to whoever receives
    # it, and an OAuth callback pointing at a laptop is rejected by the
    # provider rather than by us.
    if not _is_development():
        for name, value, breaks in (
            ('PORTAL_BASE_URL', settings.portal_base_url, 'invite links and portal links in error messages'),
            ('OAUTH_REDIRECT_BASE_URL', settings.oauth_redirect_base_url, 'OAuth provider callbacks'),
            ('OAUTH_FRONTEND_BASE_URL', settings.oauth_frontend_base_url, 'post-login redirects'),
        ):
            if 'localhost' in value or '127.0.0.1' in value:
                warnings.append(
                    f'{name} is {value!r} with APP_ENV={settings.app_env!r}, so {breaks} '
                    f"point at this machine. Set it to the deployment's public URL."
                )

    # ── A rotation left half-finished ────────────────────────────────
    #
    # While `SECRET_ENCRYPTION_KEYS_PREVIOUS` is set, the old key still
    # decrypts everything on this deployment — which is most of what
    # rotating it was for. It has to be set during a rotation and removed
    # after, and the removal is the step people skip, so it is said on
    # every boot rather than once.
    if settings.secret_encryption_keys_previous:
        count = len([k for k in settings.secret_encryption_keys_previous.split(',') if k.strip()])
        warnings.append(
            f'SECRET_ENCRYPTION_KEYS_PREVIOUS holds {count} key(s), so {count} superseded '
            f"key(s) still decrypt this deployment's stored credentials. Finish the rotation: "
            f'`python tools/rotate_secret_key.py --apply`, then unset it.'
        )

    # A malformed old key is worth catching at boot rather than on the
    # first credential read, which may be days later.
    for index, previous in enumerate(
        [k.strip() for k in (settings.secret_encryption_keys_previous or '').split(',') if k.strip()]
    ):
        try:
            Fernet(previous.encode())
        except (ValueError, TypeError):
            warnings.append(
                f'SECRET_ENCRYPTION_KEYS_PREVIOUS entry {index + 1} is not a valid Fernet key, so '
                f'anything encrypted under it cannot be read. Check for a truncated paste.'
            )

    # ── OIDC, half-configured ────────────────────────────────────────
    #
    # `/v1/auth/oidc` answers 404 unless all three of issuer, client id
    # and client secret are set — the same fail-closed shape as node
    # registration. Which is right, and silent: an operator who set two
    # of the three sees an endpoint that does not exist and concludes the
    # feature is missing rather than that their configuration is
    # incomplete. Saying so at startup costs nothing.
    oidc_parts = {
        'OIDC_ISSUER': settings.oidc_issuer,
        'OIDC_CLIENT_ID': settings.oidc_client_id,
        'OIDC_CLIENT_SECRET': settings.oidc_client_secret,
    }
    supplied = {name for name, value in oidc_parts.items() if value}
    if supplied and supplied != set(oidc_parts):
        missing = sorted(set(oidc_parts) - supplied)
        warnings.append(
            f'OIDC is partly configured: {missing} not set, so SSO is disabled and '
            f'/v1/auth/oidc answers 404. Set all three, or none.'
        )

    # An `http://` issuer is not a preference. The discovery document and
    # the signing keys arrive over that connection, so anybody who can
    # see the traffic can substitute their own keys and mint any user.
    # Fatal outside dev rather than a warning: this is the trust anchor.
    if settings.oidc_issuer and not settings.oidc_issuer.startswith('https://'):
        host = settings.oidc_issuer.removeprefix('http://').split('/')[0].split(':')[0]
        if host not in ('localhost', '127.0.0.1', '::1'):
            message = _fatal(
                f'OIDC_ISSUER is {settings.oidc_issuer!r}, which is not HTTPS. The signing keys '
                'for every SSO login arrive over that connection, so anybody who can see the '
                'traffic can substitute their own and sign in as any user.',
                'use an https issuer URL.',
            )
            if _is_development():
                warnings.append(f'INSECURE (allowed because APP_ENV=dev): {message}')
            else:
                fatal.append(message)

    return fatal, warnings


# ── The dev bypass leaves a real admin behind ─────────────────────────
#
# ``PORTAL_AUTH_BYPASS`` is refused outside dev, so the bypass cannot
# *operate* in production. But it does not only skip authentication — it
# writes rows. ``portal_auth.get_current_portal_user`` calls
# ``ensure_exists`` for a user and an organisation, so any database a
# bypass-enabled server has ever touched carries:
#
#     users:         demo-user-001 / alex@igni.sh, hashed_password ''
#     organizations: demo-org-001, owned by demo-user-001, role admin
#
# Those rows outlive the flag. A developer running locally against a
# database that is later promoted, restored into production, or simply
# reused, ships a real administrator account.
#
# Demonstrated end to end against a server with ``APP_ENV=production`` and
# ``PORTAL_AUTH_BYPASS=false``:
#
#     POST /v1/send-sign-in-code {"email": "alex@igni.sh"}   → 200
#     POST /v1/sign-in-with-code {...}                       → access_token
#     GET  /v1/portal/me         → role: admin, org demo-org-001
#     GET  /v1/portal/team       → 200
#
# The one-time code is the only thing between an attacker and that
# account, and it is not much: with ``EMAIL_ENABLED=false`` and
# ``LOG_SIGN_IN_CODES=true`` — the documented first-setup configuration
# for a deployment with no relay — codes are written to the server log
# for an operator to relay, so log access is enough.
# With email enabled it goes to a mailbox at a domain the customer does
# not control.
#
# So this refuses to start rather than warning. A warning is the wrong
# shape for a live administrator account: it scrolls past, and the
# deployment keeps serving.


async def check_no_bypass_identity_in_database(*, raise_on_unsafe: bool = True) -> list[str]:
    """Refuse to serve a database carrying the dev bypass's admin account.

    Async and separate from :func:`check_deployment` because it needs the
    database, which that function deliberately does not touch — it runs
    before anything is known to be reachable.

    A database that cannot be queried is not a finding: that is what
    ``/ready`` is for, and failing startup on a transient connection error
    would turn a blip into an outage. So this reports nothing rather than
    guessing.
    """
    if _is_development():
        return []

    try:
        found = await DeploymentChecksDB().bypass_identity_rows(user_id=BYPASS_USER_ID, org_id=BYPASS_ORG_ID)
    except Exception as exc:  # noqa: BLE001 — any driver error means "cannot tell"
        logger.warning(
            'Could not check for the dev bypass identity (%s: %s). This check is skipped, not passed.',
            type(exc).__name__,
            exc,
        )
        return []

    if not found:
        return []

    message = _fatal(
        f'This database contains the local-development bypass identity '
        f'({BYPASS_USER_ID!r} / {BYPASS_ORG_ID!r}) and APP_ENV={settings.app_env!r}. '
        f'That is a real administrator account with an empty password, created by '
        f'PORTAL_AUTH_BYPASS on some machine that pointed at this database. Anyone '
        f'who can read a one-time code — from the server log when email is disabled, '
        f'or from its mailbox — can sign in as an admin.',
        'remove both rows before serving:\n'
        f"           DELETE FROM organizations WHERE id = '{BYPASS_ORG_ID}';\n"
        f"           DELETE FROM users WHERE user_id = '{BYPASS_USER_ID}';\n"
        '         If this *is* a development database, set APP_ENV=dev.',
    )

    logger.error('Deployment check: %s', message)
    if raise_on_unsafe:
        raise UnsafeDeployment('Refusing to start:\n\n' + message)
    return [message]


async def check_pool_fits_the_server() -> list[str]:
    """Say whether this process's pool can fit alongside its siblings.

    A **warning**, never a refusal. The arithmetic depends on how many
    replicas the operator runs, which this process cannot know, so an
    exact answer is not available and refusing on a guess would stop a
    deployment that is fine. What it *can* do is state both numbers next
    to each other, which is the thing nobody has in front of them when
    they choose an instance size.

    ``pool_size + max_overflow`` is one process's ceiling. Multiply by the
    API replicas, add the worker, add the migration job and the backup and
    whatever psql session an operator has open. Cloud SQL's smallest tier
    allows 25 connections in total; the shipped defaults are 20 per
    process. That combination works until the second replica starts, and
    then fails as load arrives rather than at deploy time — which is the
    worst moment to learn it. F116.
    """
    ceiling = settings.db_pool_size + settings.db_max_overflow

    try:
        limits = await DeploymentChecksDB().connection_limits()
    except Exception as exc:  # noqa: BLE001 — any driver error means "cannot tell"
        logger.warning(
            'Could not read the database server max_connections (%s). This process will '
            'use up to %d connections; check that the server allows for it across every replica.',
            type(exc).__name__,
            ceiling,
        )
        return []

    usable = limits.usable
    logger.info(
        'Connection budget: this process may use %d (pool %d + overflow %d); '
        'the server allows %d (max_connections %d less %d reserved).',
        ceiling,
        settings.db_pool_size,
        settings.db_max_overflow,
        usable,
        limits.max_connections,
        limits.superuser_reserved,
    )

    # Two processes' worth is the smallest shape anything we ship
    # deploys — the chart's default is two API replicas, and even the
    # single-instance compose stack runs an API and a worker.
    if ceiling * 2 > usable:
        problem = (
            f'DB_POOL_SIZE + DB_MAX_OVERFLOW is {ceiling}, and the database server allows '
            f'{usable} connections in total. Two processes at that size would exhaust it, '
            f'and everything we ship runs at least two — the chart defaults to two API '
            f'replicas plus a worker. Postgres answers "too many clients already" and '
            f'refuses the connection, which surfaces as 500s from whichever replica '
            f'started second. Either lower DB_POOL_SIZE/DB_MAX_OVERFLOW, or raise the '
            f"server's max_connections (a managed instance may need a larger tier: Cloud "
            f"SQL's smallest allows 25)."
        )
        logger.warning('Deployment check: %s', problem)
        return [problem]

    return []
