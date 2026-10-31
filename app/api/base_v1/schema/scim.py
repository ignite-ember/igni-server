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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

USER_SCHEMA = 'urn:ietf:params:scim:schemas:core:2.0:User'
LIST_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:ListResponse'
ERROR_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:Error'
PATCH_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:PatchOp'


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


class ScimError(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: [ERROR_SCHEMA])
    detail: str = ''
    status: str = '400'
    #: RFC 7644 §3.12's typed reasons. Only the one that matters here:
    #: a provider retrying a create for somebody who already exists
    #: needs to tell that from a malformed request.
    scimType: Literal['uniqueness', 'invalidValue', 'invalidFilter'] | None = None
