"""The shapes SCIM 2.0 speaks (RFC 7643, RFC 7644).

Written out rather than generated, and deliberately smaller than the
specification: an identity provider sends a handful of attributes and
ignores the rest, so modelling everything would be modelling a
conversation nobody has. What is here is what Okta and Entra actually
put on the wire.

Two conventions from the RFCs that look like mistakes and are not:

* ``Resources`` and ``Operations`` are capitalised. SCIM attribute
  names are case-insensitive on input but these are the canonical
  spellings, and a provider matching case-sensitively on its own
  output is a real thing.
* every payload names its ``schemas``. A provider reads that before
  it reads anything else.

Unknown attributes are **accepted and ignored** rather than refused.
A provider that sends ``phoneNumbers`` to a server that does not store
them should provision the user, not fail the sync — and a 400 there
is a provisioning run an administrator has to go and look at for no
reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.sql.models.user import User
from app.services.auth.person_name import split_full_name

USER_SCHEMA = 'urn:ietf:params:scim:schemas:core:2.0:User'
LIST_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:ListResponse'
ERROR_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:Error'
PATCH_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:PatchOp'
SERVICE_PROVIDER_CONFIG_SCHEMA = 'urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig'


class Name(BaseModel):
    """A person's name, in the three parts SCIM splits it into."""

    model_config = ConfigDict(extra='ignore')

    givenName: str = ''
    familyName: str = ''
    formatted: str = ''


class Email(BaseModel):
    model_config = ConfigDict(extra='ignore')

    value: str = ''
    primary: bool = False
    type: str = ''


class Meta(BaseModel):
    resourceType: str = 'User'
    created: str | None = None
    lastModified: str | None = None
    location: str | None = None


class ScimUser(BaseModel):
    """A user as a provider sends and receives one.

    ``extra='ignore'`` is the point: see the module note on unknown
    attributes.
    """

    model_config = ConfigDict(extra='ignore')

    schemas: list[str] = Field(default_factory=lambda: [USER_SCHEMA])
    id: str | None = None
    #: The provider's own identifier for this person. Stored nowhere —
    #: accounts are keyed by address here — but echoed back, because a
    #: provider matches its records against it.
    externalId: str | None = None
    #: The address. Every provider sends it; it is what an account is
    #: keyed on here.
    userName: str = ''
    name: Name = Field(default_factory=Name)
    displayName: str = ''
    emails: list[Email] = Field(default_factory=list)
    #: False is the deprovisioning signal, and the reason this exists.
    active: bool = True
    meta: Meta = Field(default_factory=Meta)

    @classmethod
    def of_account(cls, user: User) -> ScimUser:
        """One of our accounts, as a provider expects to read it."""
        full_name = user.full_name or ''
        given, family = split_full_name(full_name)
        return cls(
            id=user.user_id,
            userName=user.email,
            displayName=full_name,
            name=Name(givenName=given, familyName=family, formatted=full_name),
            emails=[Email(value=user.email, primary=True, type='work')],
            # The one attribute a provisioning run is really asking about.
            active=user.deactivated_at is None,
            meta=Meta(
                resourceType='User',
                created=_iso(user.created_at),
                lastModified=_iso(user.updated_at),
                location=f'/scim/v2/Users/{user.user_id}',
            ),
        )

    @property
    def address(self) -> str:
        """The address to key on, from wherever the provider put it.

        ``userName`` is where it belongs and where every provider puts it.
        The primary email is the documented fallback for the ones that
        send a login name there instead, and taking the first email is the
        last resort — a provider that sends exactly one is not ambiguous
        about which it means.
        """
        if '@' in self.userName:
            return self.userName.strip().lower()
        primary = next((e.value for e in self.emails if e.primary and e.value), '')
        fallback = primary or next((e.value for e in self.emails if e.value), '')
        return fallback.strip().lower()

    @property
    def full_name(self) -> str:
        """The display name, else the formatted name, else given and family joined; ``''`` when none is sent."""
        return self.displayName or self.name.formatted or f'{self.name.givenName} {self.name.familyName}'.strip()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class ListResponse(BaseModel):
    """What a filtered query answers with, even for one result."""

    schemas: list[str] = Field(default_factory=lambda: [LIST_SCHEMA])
    totalResults: int = 0
    startIndex: int = 1
    itemsPerPage: int = 0
    Resources: list[ScimUser] = Field(default_factory=list)


class PatchOperation(BaseModel):
    model_config = ConfigDict(extra='ignore')

    #: Providers send these capitalised, lowercase, and mixed. Compared
    #: case-insensitively where it is read.
    op: str = ''
    path: str | None = None
    value: Any = None

    def active(self) -> bool | None:
        """What this operation sets ``active`` to, or ``None`` when it does not set it.

        Entra names the attribute in ``path`` with a scalar value; Okta
        sends no path and an object.
        """
        if self.op and self.op.lower() not in ('replace', 'add'):
            return None
        path = (self.path or '').strip().lower()
        if path == 'active':
            return _as_bool(self.value)
        answer: bool | None = None
        if not path and isinstance(self.value, dict):
            for key, value in self.value.items():
                if key.lower() == 'active':
                    answer = _as_bool(value)
        return answer


def _as_bool(value: Any) -> bool:
    """A provider may send a JSON boolean or the string form of one."""
    if isinstance(value, str):
        return value.strip().lower() == 'true'
    return bool(value)


class PatchRequest(BaseModel):
    """``PATCH /Users/{id}`` — how a provider deprovisions somebody.

    The operation that matters is ``replace`` of ``active`` to false.
    Entra sends ``{"op": "Replace", "path": "active", "value": false}``
    and Okta sends ``{"op": "replace", "value": {"active": false}}``;
    both forms are read, because supporting one of the two means
    supporting one of the two providers.
    """

    model_config = ConfigDict(extra='ignore')

    schemas: list[str] = Field(default_factory=lambda: [PATCH_SCHEMA])
    Operations: list[PatchOperation] = Field(default_factory=list)

    def active_change(self) -> bool | None:
        """Whether this patch sets ``active``, and to what; the last operation that does wins.

        ``None`` when it says nothing about it — a provider syncing a
        display name must not be read as a deprovisioning.
        """
        answer: bool | None = None
        for operation in self.Operations:
            active = operation.active()
            if active is not None:
                answer = active
        return answer


class ScimError(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: [ERROR_SCHEMA])
    detail: str = ''
    status: str = '400'
    #: RFC 7644 §3.12's typed reasons. Only the one that matters here:
    #: a provider retrying a create for somebody who already exists
    #: needs to tell that from a malformed request.
    scimType: Literal['uniqueness', 'invalidValue', 'invalidFilter'] | None = None


class Supported(BaseModel):
    """RFC 7643 §5's ``{"supported": …}`` for a feature with no settings."""

    supported: bool


class BulkSupport(BaseModel):
    supported: bool = False
    maxOperations: int = 0
    maxPayloadSize: int = 0


class FilterSupport(BaseModel):
    supported: bool = True
    maxResults: int = 200


class AuthenticationScheme(BaseModel):
    type: str
    name: str
    description: str
    primary: bool = False


class ServiceProviderConfig(BaseModel):
    """What this implementation supports (RFC 7643 §5). Providers read it first.

    The defaults *are* this server's answer, so the route serves a bare
    instance. Unsupported features are said plainly rather than left for
    a provider to discover by having a run fail.

    No ``documentationUri``. It is optional, and the one URL worth
    putting there is the vendor's — which is a host named in ``app/``
    for a convenience link, on a product whose rule is that there are
    none. The operator reads how to configure this where they configure
    it: ``dist.env`` and the chart.
    """

    schemas: list[str] = Field(default_factory=lambda: [SERVICE_PROVIDER_CONFIG_SCHEMA])
    patch: Supported = Field(default_factory=lambda: Supported(supported=True))
    bulk: BulkSupport = Field(default_factory=BulkSupport)
    filter: FilterSupport = Field(default_factory=FilterSupport)
    changePassword: Supported = Field(default_factory=lambda: Supported(supported=False))
    sort: Supported = Field(default_factory=lambda: Supported(supported=False))
    etag: Supported = Field(default_factory=lambda: Supported(supported=False))
    authenticationSchemes: list[AuthenticationScheme] = Field(
        default_factory=lambda: [
            AuthenticationScheme(
                type='oauthbearertoken',
                name='OAuth Bearer Token',
                description='A token configured on both sides; see scim_token.',
                primary=True,
            )
        ]
    )


class ActiveChangedAudit(BaseModel):
    """The audit detail a provider's change to somebody's ``active`` leaves.

    Only the outcome — never the request body, which can carry
    attributes this server does not store and must not retain.
    """

    active: bool
