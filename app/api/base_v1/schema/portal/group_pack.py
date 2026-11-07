"""The signed CLI pack: what igni receives from the group its person is in.

The contract the igni CLI consumes. `SignedOrgGroupPack`'s signature
covers this payload, so field order and serialisation are part of it.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

from app.api.base_v1.schema.portal.group_entries import OrgGroupPackEntry
from app.api.base_v1.schema.portal.group_theme import GroupTheme


class OrgGroupPack(BaseModel):
    """Full pack of entries for a group — consumed by igni CLI."""

    group_id: str
    group_name: str
    fetched_at: datetime
    entries: list[OrgGroupPackEntry]
    # What the group's people get when an agent names no model.
    default_model: str | None = None
    # Brand overrides, applied by the client's UI. Group-level like
    # ``default_model`` rather than an entry, and delivered the same
    # way — this pack is already polled with an ETag, so branding costs
    # no extra request and an unchanged theme costs no bytes.
    theme: GroupTheme | None = None
    #: The organisation the group belongs to. Not part of the served
    #: payload (excluded from `as_payload`, so the response and its ETag
    #: are what they were); carried so the signed form can name it.
    org_id: str | None = Field(default=None, exclude=True)

    def as_payload(self) -> dict:
        """The pack as JSON-ready types.

        `mode='json'`, so `fetched_at` leaves as an ISO string. Python
        mode hands `JSONResponse`'s encoder a raw `datetime`, which it
        refuses — and that is a property of this model's fields, so it
        belongs here rather than in a comment beside one caller.
        """
        return self.model_dump(mode='json')

    def etag(self, *, bound_to: str = '') -> str:
        """A validator for "has this pack changed".

        **`fetched_at` is excluded, and that is the whole point.** It
        is `now()`, so a tag over the whole payload differs on every
        request: present, well-formed, and unable to match by
        construction. The tag covers what identifies the pack, not
        when it was read.

        `usedforsecurity=False` is not only documentation. This is a
        cache validator and not a signature, and on a FIPS-enabled
        host a bare `hashlib.md5()` raises — which would make this a
        500 on exactly the kind of regulated deployment a self-hosted
        product is installed into.

        `bound_to` folds in what the signed form depends on besides the
        content: who it was signed for and with which key (see
        `GroupPackSigner.binding`). Without it, somebody else signing in
        on the same machine, or the first poll after a key rotation,
        would get a 304 and keep a signature that no longer verifies.
        """
        identity: dict = {key: value for key, value in self.as_payload().items() if key != 'fetched_at'}
        if bound_to:
            identity['_bound_to'] = bound_to
        digest = hashlib.md5(json.dumps(identity, sort_keys=True).encode(), usedforsecurity=False)
        return f'"{digest.hexdigest()}"'


class SignedOrgGroupPack(OrgGroupPack):
    """What `GET /v1/portal/me/group/pack` serves: the pack, and the pack signed.

    Every field of `OrgGroupPack` is still here, unchanged, for clients
    that read them. `signed_pack` is the same content as a compact JWS
    (`app.services.portal.group_pack_signature`); igni applies only that.
    """

    signed_pack: str


class SignedNoPack(BaseModel):
    """What `GET /v1/portal/me/group/pack` serves to a person with no pack.

    Signed for that person (`GroupPackSigner.sign_absence`), so igni can
    clear the policy it cached rather than ignore an unsigned `null`.
    """

    no_pack: Literal[True] = True
    signed_pack: str
