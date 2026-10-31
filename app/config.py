from typing import Literal
from urllib.parse import quote_plus, unquote

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_PATH = '/'

# Git's empty tree SHA - represents a tree with no files
# This is a universal constant in Git, same across all repositories
# Used for comparing initial commits that have no parent
GIT_EMPTY_TREE_SHA = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'

# Constant owner user_id — used for admin/system API calls.
# Deterministic UUID5(NAMESPACE_DNS, 'ember-owner-system').
OWNER_USER_ID = '771339b1-4b44-50e5-970c-c4bd559c3912'


class CloudVmConfig(BaseModel):
    """What a VM gets until an admin says otherwise (`docs/service-identity.md` §12).

    `standard_*`: the organisation's default profile ("Standard") when it is first made, and a new
    profile's value for any field its body leaves out. `cap_*`: the platform's caps until a super admin
    sets them. Environment: `CLOUD_VM__STANDARD_MEMORY_MIB=4096` and so on.
    """

    standard_name: str = Field(default='Standard', min_length=1, max_length=60)
    standard_memory_mib: int = Field(
        default=4096, ge=128, description='The most memory a VM may have: its size at boot'
    )
    standard_normal_memory_mib: int = Field(
        default=1024,
        ge=128,
        description='What a VM normally has (igni idle uses ~220 MB); the balloon holds the rest back',
    )
    standard_vcpus: int = Field(default=1, ge=1)
    standard_idle_before_pause_s: int | None = Field(default=10, ge=0, description='None: never paused')
    standard_volume_gib: int = Field(default=10, ge=1)
    standard_volume_max_gib: int = Field(default=50, ge=1)
    cap_max_memory_mib: int = Field(default=8192, ge=128)
    cap_max_vcpus: int = Field(default=4, ge=1)
    cap_max_idle_before_pause_s: int | None = Field(default=3600, ge=0, description='None: any, never included')
    cap_max_volume_gib: int = Field(default=200, ge=1)


class AIConfig(BaseModel):
    """How the API talks to inference backends. Environment: `AI__UPSTREAM_TIMEOUT=300`."""

    upstream_timeout: float = Field(default=300.0, description='Timeout in seconds for upstream AI provider requests')


class OtelConfig(BaseSettings):
    """OpenTelemetry export, to the customer's own collector (docs/observability.md §5).

    Read from the standard ``OTEL_*`` environment variables and from nothing
    else — not the ``.env`` file the rest of the settings read, not the
    database, not an admin screen — so it is the operator's decision, made
    where the process is started. There is **no default endpoint**: with
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` unset nothing is exported and no tracer
    provider is built (``app/observability/otel.py``).
    """

    model_config = SettingsConfigDict(env_prefix='OTEL_', case_sensitive=False, extra='ignore')

    #: The collector's base URL; for ``http/protobuf`` the signal path
    #: (``/v1/traces``) is appended. Empty: nothing is exported.
    exporter_otlp_endpoint: str = ''
    exporter_otlp_protocol: Literal['grpc', 'http/protobuf'] = 'http/protobuf'
    #: ``key=value,key2=value2``, values URL-encoded — for the collector's authentication.
    exporter_otlp_headers: str = ''
    service_name: str = 'igni-server'

    @property
    def endpoint(self) -> str:
        return self.exporter_otlp_endpoint.strip()

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint)

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        for pair in self.exporter_otlp_headers.split(','):
            key, sep, value = pair.partition('=')
            if sep and key.strip():
                headers[key.strip().lower()] = unquote(value.strip())
        return headers


class Settings(BaseSettings):
    model_config = {
        'env_file': '.env',
        'env_file_encoding': 'utf-8',
        'case_sensitive': False,
        'env_nested_delimiter': '__',
        'extra': 'ignore',
    }

    app_name: str = 'igni.sh: API'
    # One version, and `tests/test_one_version_number.py` keeps it that
    # way. F1 found three: this said 0.5.0, `pyproject.toml` said 0.6.0
    # and the chart's `appVersion` said 0.1.0 — and the chart's default
    # `image.tag` is its `appVersion`, so the default install asked for
    # a tag nothing had ever published.
    app_version: str = '0.6.0'
    app_configure_logging_level: str = 'INFO'
    app_root_path: str = ROOT_PATH
    log_level: str = 'INFO'
    app_env: str = Field(default='prod', description='Environment name: dev, stage, prod')

    # ── Email ────────────────────────────────────────────────────────
    # Off unless configured, and pointing nowhere by default.
    #
    # These used to default to ``True`` and ``smtp.gmail.com``, which was
    # right for one hosted deployment and wrong for every self-hosted
    # one. A customer who configured nothing got a service convinced it
    # could send mail, trying to reach Gmail from a network with no route
    # to it — and ``EmailService`` logs and swallows every failure
    # inside a background task, so each attempt died where nobody was
    # looking while the user was told to check an inbox.
    #
    # Opting in to email is a deliberate act. Opting out must not be.
    # Every flow that uses email has a defined answer for "and if it
    # cannot be sent?" — see ``app/services/email/delivery.py``.
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_username: str = ''
    smtp_password: str = ''
    email_from_address: str = 'noreply@igni.local'
    email_enabled: bool = False

    # With no relay, write one-time codes (sign-in, sign-up, password
    # reset, address verification) to the server log so an operator can
    # hand them on. **Off by default** (F35): whoever can read the logs —
    # usually a wider group than should be able to sign in as anybody,
    # for as long as the aggregator keeps them — could otherwise sign in
    # as any user and reset any password, on a deployment that never
    # chose that. Turn it on for first setup without email, then turn it
    # off once a relay is configured. The server warns at startup and on
    # every code it logs while this is on. Has no effect while a relay is
    # configured: codes are emailed then, never logged.
    log_sign_in_codes: bool = Field(
        default=False,
        description='With no mail relay, write one-time codes to the server log. Credential material: first setup only',
    )
    # ``public_beta`` gates nothing. It is referenced by one test fixture
    # and by no application code, and is kept only because removing a
    # setting a deployment may have set is a separate decision from this
    # one. See F113.
    public_beta: bool = True

    # Refuse gmail and its peers at sign-up.
    #
    # On by default because ``domain`` visibility — "share this with my
    # colleagues" — decides who a colleague is by comparing the part of
    # an address after the ``@``. That is only a useful question while a
    # domain names an employer; once two strangers can sign up at
    # ``gmail.com``, sharing with "my domain" shares with both of them.
    #
    # Turning it off is legitimate for a single-person install, and
    # means accepting that domain visibility groups nobody usefully.
    # See ``app/security/account_domains.py``.
    block_public_mail_domains: bool = True

    # How to secure the connection. The three cases are genuinely
    # different servers, not preferences:
    #
    # * ``starttls`` — connect plain, upgrade. Port 587. What Amazon SES,
    #   Google Workspace and most hosted relays expect.
    # * ``tls`` — TLS from the first byte. Port 465. Also offered by SES,
    #   and the only option on some corporate gateways.
    # * ``none`` — no encryption. Port 25. An internal relay on a private
    #   network, which is what an air-gapped deployment usually has.
    #
    # aiosmtplib treats the first two as mutually exclusive, so this
    # cannot be two booleans without inviting a combination that throws.
    smtp_tls_mode: Literal['starttls', 'tls', 'none'] = Field(
        default='starttls', description='starttls (587), tls (465), or none (25, internal relay)'
    )

    # The display name on outbound mail. Configurable because a
    # self-hosted deployment is the customer's, not ours — their people
    # should not receive sign-in codes from a vendor's brand.
    email_from_name: str = Field(default='igni', description='Display name in the From header')

    # ── Which transport carries the mail ─────────────────────────────
    # ``smtp`` works against anything, including SES. ``ses`` uses the
    # SES API instead, and the reason to choose it is credentials rather
    # than email: the AWS SDK resolves an instance profile, task role or
    # pod identity, so the deployment stores no mail secret at all.
    email_backend: Literal['smtp', 'ses'] = Field(
        default='smtp', description='smtp (any relay, incl. SES) or ses (SES API with IAM credentials)'
    )
    ses_region: str = Field(default='', description='AWS region for the SES API; empty uses the SDK default')
    ses_configuration_set: str = Field(
        default='', description='Optional SES configuration set, for bounce and complaint handling'
    )

    # Static AWS credentials. Leave empty — which is the point. boto3
    # then walks its normal chain (environment, shared config, container
    # credentials endpoint, IMDS), so a deployment on EC2/ECS/EKS
    # authenticates with its role and rotates without us. Set these only
    # where no role is available.
    aws_access_key_id: str = Field(default='', description='Leave empty to use the ambient AWS credential chain')
    aws_secret_access_key: str = Field(default='', description='Only meaningful alongside aws_access_key_id')

    # Base URL of the portal, used to build action links — invite
    # acceptance, password reset. Every self-hosted deployment must set
    # this to whatever its users type into a browser.
    #
    # The default is a working local one rather than a hosted hostname
    # the customer has never heard of. It is wrong for any real install,
    # but wrong in a way that is obvious the first time somebody opens an
    # invite link, instead of pointing at a domain that resolves for
    # somebody else.
    portal_base_url: str = 'http://localhost:3000'

    # Bearer token the identity provider presents to /scim/v2. Empty
    # disables SCIM entirely — every route answers 503: a provisioning
    # API that accepts unauthenticated callers can create and
    # deprovision anybody.
    #
    # Its own credential, not a user's. SCIM is the IdP acting as
    # itself, on a schedule, with nobody signed in; a token tied to a
    # person would stop working the day that person left, which is the
    # one thing provisioning must survive.
    scim_token: str = Field(default='', description='Bearer token the identity provider presents to the SCIM API')

    # `changeset_gcs_bucket` and `changeset_gcs_prefix` lived here. They
    # went with the changeset pipeline to igni__code-index, which owns
    # object storage now — see docs/service-contract.md, which listed
    # them as deleted from this repo before they actually were. Nothing
    # in this codebase talks to object storage; there is no cloud SDK in
    # the dependency set. Do not reintroduce a required setting for a
    # subsystem that lives elsewhere: this one was the only field in the
    # whole configuration with no default, and it stopped every
    # deployment outside Google Cloud from starting at all.

    # `text` for a person reading `docker logs`, `json` for a log
    # shipper. Nothing is sent anywhere either way — this changes the
    # shape of what a customer's own collector picks up. See
    # app/observability/log_format.py.
    log_format: str = Field(default='text', description='Log output format: text or json')

    # Verification codes an anonymous caller can make this deployment
    # send, across all addresses, per hour. Per-address limits are always
    # on and are not configurable; this is the backstop against spraying
    # across many addresses.
    #
    # Generous by default and `0` disables it: an organisation onboarding
    # three hundred people in an afternoon must not find sign-in switched
    # off, and that failure is worse than the abuse it prevents. See
    # app/services/auth/code_throttle.py.
    email_codes_hourly_limit: int = Field(
        default=600, description='Anonymous verification codes sent per hour across all addresses; 0 disables'
    )

    # Prometheus scraping. Empty means `GET /metrics` answers 404: the
    # exposition names every route in the product and the version each
    # replica runs, which is not something to publish because nobody
    # thought about it. See app/monitoring/router.py.
    metrics_token: str = Field(default='', description='Bearer token a Prometheus scrape must present to read /metrics')

    # Where this server is reached from outside — the `iss` of every
    # token it signs, and the one URL a service like CodeIndex or
    # Artifacts is deployed with: it finds the published keys and every
    # endpoint from here. The signing key itself is not a setting; the
    # server generates it (see app/services/identity/key_ring.py).
    public_base_url: str = Field(
        default='http://localhost:8000', description="This server's external URL; the issuer of its tokens"
    )

    # Postgres
    postgres_user: str = Field(default='myuser')
    postgres_password: str = Field(default='mysecretpassword')
    postgres_db: str = Field(default='igni')
    postgres_host: str = Field(default='postgres')
    postgres_port: int = Field(default=5432)

    # ── The connection pool ──────────────────────────────────────────
    #
    # One pool per process, shared by every repository — see
    # `app/db/sql/__init__.py` for why it was thirteen of them and what
    # that cost. `pool_size + max_overflow` is therefore the **whole**
    # process's ceiling on concurrent queries, and
    # `(size + overflow) x processes` has to fit inside the Postgres
    # server's `max_connections`.
    #
    # The defaults suit the shipped shape: 2 API replicas and 1 worker,
    # 20 connections each, 60 of the 100 a default Postgres allows, with
    # the rest left for migrations, the backup job and an operator's
    # psql. A managed instance may allow far fewer — Cloud SQL's smallest
    # tier permits 25 — so a startup check compares these numbers against
    # what the server actually reports and says so.
    db_pool_size: int = Field(
        default=5, ge=1, description='Connections kept open per process. Raise for a query-heavy worker.'
    )
    db_max_overflow: int = Field(
        default=15, ge=0, description='Extra connections allowed above the pool size during a burst.'
    )
    # 30 seconds — SQLAlchemy's default — means a caller waits half a
    # minute and then gets a 500, by which time the client has usually
    # given up and retried, adding load to a pool that is already full.
    # Failing sooner sheds load instead of hoarding it.
    db_pool_timeout: float = Field(
        default=10.0, gt=0, description='Seconds a request waits for a free connection before failing.'
    )

    # Redis
    redis_host: str = Field(default='redis')
    redis_port: int = Field(default=6379)
    redis_db: int = Field(default=10)

    # MCP Gateway
    mcp_gateway_url: str = Field(default='http://mcp_gateway:7778', description='MCP Gateway service URL')

    # ── Secrets at rest ──────────────────────────────────────────────
    # Fernet key encrypting the secrets this deployment stores in its own
    # database. Mint with ``Fernet.generate_key()``.
    #
    # Empty means those write paths refuse rather than store plaintext —
    # a clear "this server is not configured" beats a silent downgrade.
    secret_encryption_key: str = Field(
        default='',
        description='Fernet key encrypting stored secrets at rest',
    )

    # Decrypt-only keys, comma-separated. Set during a rotation so both
    # the old and the new key work, then removed once
    # `python tools/rotate_secret_key.py --apply` has re-encrypted the
    # stored rows. While this is set, the old key is still a working key
    # for this deployment — which is most of what rotating was for — so
    # the startup checks say so on every boot.
    #
    # Comma-separated rather than a JSON list because a person types this
    # into an env file mid-rotation.
    secret_encryption_keys_previous: str = Field(
        default='',
        description='Comma-separated Fernet keys accepted for decryption only, during a rotation',
    )

    # ── Licence ──────────────────────────────────────────────────────
    # The paid modules' licence file (docs/licence-file.md). The same file
    # is mounted into igni server and every paid module; igni server only
    # reads it to show where each module stands and to install an upload.
    # The base needs no licence and ignores a missing file.
    igni_licence_file: str = Field(
        default='/etc/igni/licence/licence.json',
        description="Path of the paid modules' licence file, shared with every module",
    )

    # OAuth (social sign-in)
    oauth_google_client_id: str = Field(default='')
    oauth_google_client_secret: str = Field(default='')
    oauth_redirect_base_url: str = Field(
        default='http://localhost:5173', description='API base URL for OAuth provider callback URIs'
    )
    oauth_frontend_base_url: str = Field(
        default='http://localhost:5173', description='Frontend base URL for post-login redirect'
    )
    # Where a shared document opens. Its own application, not a
    # section of the portal: the portal configures a deployment,
    # this is what the deployment exists to carry, and a recipient
    # arriving from a link wants a document, not an admin console.
    #
    # Empty falls back to the portal's base URL, so a deployment
    # that has not been told about the documents app keeps handing
    # out links that work.
    documents_base_url: str = Field(default='', description='Base URL of the documents app; empty uses the portal URL')
    # Where a shared link actually points.
    #
    # igni itself, when a deployment says where that is. Whoever
    # follows one is a colleague who has igni, and they want the
    # document where their own documents are — beside the tree they
    # already know and the conversation they are about to join — not
    # in a separate reader that can only show them the page.
    #
    # Empty falls back to the documents app, which is the right answer
    # for a deployment without one: a reader that shows the document
    # beats a link to an application the recipient may not be running.
    app_base_url: str = Field(default='', description='Base URL of the igni app; empty uses the documents app')
    # ── OIDC (the customer's own identity provider) ──────────────────
    # The three providers above are specific companies, reachable only
    # with internet access. This is what an air-gapped deployment uses:
    # Keycloak, Okta, Entra, Auth0, Authentik. One issuer configures it,
    # because discovery publishes the rest.
    oidc_issuer: str = Field(default='', description='OIDC issuer URL; empty disables OIDC sign-in')
    oidc_client_id: str = Field(default='')
    oidc_client_secret: str = Field(default='')
    oidc_scopes: str = Field(default='openid email profile', description='Scopes requested at authorize')
    # Providers disagree about where the address lives — usually
    # ``email``, sometimes ``preferred_username`` or ``upn``. Guessing
    # wrong means every user signs in with no address.
    oidc_email_claim: str = Field(default='email', description='Claim holding the user address')
    oidc_name_claim: str = Field(default='name', description='Claim holding the display name')
    # Whether an OIDC login may attach to an existing local account with
    # the same address. Off by default: it is an account-takeover path
    # if the IdP does not verify addresses, so it is opt-in per
    # deployment rather than assumed.
    oidc_link_by_email: bool = Field(
        default=False, description='Allow an OIDC login to claim an existing account by email'
    )

    # Which social sign-ins to offer. SSO and email are what a company
    # authenticates with; this list is the exception, not the strategy.
    # Google is the only one: GitHub and GitLab belong to CodeIndex, which
    # connects repositories, and are not a way into this deployment.
    oauth_providers: list[str] = Field(
        default=['google'],
        description='Social sign-ins to offer. SSO and password are configured separately',
    )

    # What the SSO button says. "Sign in with SSO" is accurate and
    # unhelpful — people recognise their own provider's name, and an
    # administrator rolling this out wants the button to say what they
    # told everybody it would say.
    oidc_display_name: str = Field(default='SSO', description='Label for the OIDC sign-in button')

    # Portal
    portal_auth_bypass: bool = Field(default=False, description='Bypass portal auth (dev only)')

    # ── Audit trail retention ────────────────────────────────────────
    #
    # 0 means keep everything, and that is the default deliberately. An
    # audit table needs a retention limit — GDPR Article 5(1)(e) is
    # storage limitation and an auditor will ask for the number — but a
    # default that silently discarded history would be the wrong way
    # round. Setting it is the operator's policy decision; `prune()` is
    # a no-op until they make it.
    audit_retention_days: int = Field(
        default=0, description='Delete audit events older than this many days. 0 keeps everything.'
    )

    # ── Request limits ───────────────────────────────────────────────
    #
    # Nothing bounded request size anywhere. Measured: a 193 MB chat body
    # was read and parsed in full, grew RSS by ~135 MB, and then answered
    # 404 over the model name — the size never entered into it. With rate
    # limiting deliberately absent, one authenticated caller is enough.
    #
    # 32 MB is generous on purpose: this is a coding assistant, and a
    # large-context request is legitimate. 1M tokens of text is roughly
    # 4 MB, so this leaves several times the largest real context while
    # still refusing the case above.
    max_request_body_bytes: int = Field(
        default=32 * 1024 * 1024,
        gt=0,
        description='Largest accepted request body; larger gets a 413 before being read.',
    )

    # ── Inference backends ───────────────────────────────────────────
    # The customer's own vLLM nodes, described by a JSON array. A file is
    # the normal case (a ConfigMap or bind mount, edited by whoever runs
    # the cluster); the inline form is for compose and single-node
    # installs where a mount is more ceremony than it is worth. The file
    # wins when both are set.
    inference_backends_path: str = Field(
        default='', description='Path to a JSON file describing the inference backends'
    )
    inference_backends: str = Field(
        default='', description='The same JSON inline, for deployments without a file mount'
    )

    # Nodes that announce themselves at runtime, for clusters that are
    # started and stopped rather than always on. Unset means the
    # registration API does not exist at all — a deployment describing
    # its GPUs statically should not be running an endpoint that accepts
    # new inference targets. Its own secret: it is handed to whatever
    # automation starts the cluster.
    node_registration_token: str = Field(
        default='', description='Shared secret GPU nodes present to register themselves; empty disables the API'
    )

    # Super-admin (application-wide admin, not org-level)
    service_events_per_minute: int = Field(
        default=600,
        ge=0,
        description=(
            'Events one service may ask igni to deliver to producers per minute (POST /v1/service-events). '
            '0 disables the limit.'
        ),
    )
    service_event_max_wait_seconds: int = Field(
        default=1_209_600,
        ge=1,
        description=(
            'How long an undelivered event waits, at most (14 days). An event a service sends without '
            'expires_at gets this; a later expires_at is cut to it. Then it expires: it is no longer '
            'handed out, and the service reads its status as expired.'
        ),
    )
    service_event_retention_days: int = Field(
        default=30,
        ge=0,
        description=(
            'Days an event is kept after it was delivered, expired or dropped, so its service can read '
            'what became of it; then the retention round deletes it. 0 keeps them.'
        ),
    )
    producer_feed_retention_days: int = Field(
        default=30,
        ge=0,
        description=(
            "Days a producer that is gone stays in the gateway's feed after the gateway was handed it "
            'inactive; then the retention round deletes it. 0 keeps them.'
        ),
    )
    producer_export_retention_days: int = Field(
        default=7,
        ge=1,
        description=(
            "Days an export of a removed member's data is kept for its organisation to download; then "
            'the retention round deletes it.'
        ),
    )
    producer_export_max_bytes: int = Field(
        default=2 * 1024**3,
        ge=1,
        description='The largest export archive igni server keeps, in bytes.',
    )
    chat_queue_max_wait_seconds: int = Field(
        default=900,
        ge=1,
        description=(
            'How long a model request waits for a free slot when every node is full, before it is '
            'answered 429. Long by design: an agent run waits its turn rather than failing.'
        ),
    )
    chat_queue_max_length: int = Field(
        default=2000,
        ge=0,
        description='The most model requests that may wait in one API replica; past it, a 429 at once.',
    )
    super_admin_password_hash: str = Field(default='', description='Bcrypt hash of super-admin password')

    # AI Configuration (nested)
    ai: AIConfig = Field(default_factory=AIConfig)
    # What VMs get until an admin says otherwise (nested)
    cloud_vm: CloudVmConfig = Field(default_factory=CloudVmConfig)
    # OpenTelemetry: the standard OTEL_* variables, and only those.
    otel: OtelConfig = Field(default_factory=OtelConfig)

    @property
    def _pg_password_encoded(self) -> str:
        """URL-encoded password safe for use in connection URIs."""
        return quote_plus(self.postgres_password)

    @property
    def postgres_url(self) -> str:
        """Async PostgreSQL connection URL (for application use)"""
        return f'postgresql+asyncpg://{self.postgres_user}:{self._pg_password_encoded}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}'

    @property
    def postgres_url_sync(self) -> str:
        """Synchronous PostgreSQL connection URL (for migrations)"""
        return f'postgresql+psycopg2://{self.postgres_user}:{self._pg_password_encoded}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}'

    @property
    def postgres_default_url(self) -> str:
        """Connection URL to the default 'postgres' database (used for creating databases)"""
        return f'postgresql+psycopg2://{self.postgres_user}:{self._pg_password_encoded}@{self.postgres_host}:{self.postgres_port}/postgres'

    @property
    def redis_url(self) -> str:
        """Redis connection URL.

        Said "for Celery" until DEC-13 removed the worker. It is the
        application's own Redis — the auth handoff, OAuth state and the
        rate-limit counters — and the readiness probe's client.
        """
        return f'redis://{self.redis_host}:{self.redis_port}/{self.redis_db}'


settings = Settings()
