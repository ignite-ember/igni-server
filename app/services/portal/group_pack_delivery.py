"""The answer to igni's pack poll: a signed pack, a signed "no pack", or "not modified"."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from app.api.base_v1.schema.portal.group_pack import SignedNoPack, SignedOrgGroupPack
from app.services.portal.group_pack_service import GroupPackService
from app.services.portal.group_pack_signature import GroupPackSigner


@dataclass(frozen=True)
class PackAnswer:
    """The ETag of the current answer and the body, which is `None` when the client already holds it."""

    etag: str
    body: SignedOrgGroupPack | SignedNoPack | None = None


class GroupPackDelivery:
    """Builds the pack answer for one account, signing only when the client lacks it.

    igni polls every few minutes from every running session, and the
    answer is almost always "same as before". The ETag is computed first
    and the signature only when it does not match, so the common poll is
    a 304 with no body and no signing.
    """

    def __init__(self, *, packs: GroupPackService | None = None, signer: GroupPackSigner | None = None) -> None:
        self._packs = packs or GroupPackService()
        self._signer = signer or GroupPackSigner()

    async def answer(self, *, user_id: str, org_id: str | None, held_etag: str | None) -> PackAnswer:
        """The pack for the group `user_id` is in within `org_id`, revalidated against `held_etag`.

        The ETag is bound to what the signature depends on (who it is
        signed for, and with which key — `GroupPackSigner.binding`), so a
        304 never leaves a client holding a pack signed for somebody else
        or with a key that has since been rotated out.
        """
        pack = await self._packs.get_my_group_pack(user_id=user_id, org_id=org_id)
        if pack is None:
            etag = self._absent_etag(self._signer.binding(subject=user_id, org_id=org_id))
            if held_etag == etag:
                return PackAnswer(etag=etag)
            return PackAnswer(
                etag=etag,
                body=SignedNoPack(signed_pack=self._signer.sign_absence(subject=user_id, org_id=org_id)),
            )

        etag = pack.etag(bound_to=self._signer.binding(subject=user_id, org_id=pack.org_id))
        if held_etag == etag:
            return PackAnswer(etag=etag)
        signed = self._signer.sign(pack, subject=user_id, org_id=pack.org_id)
        return PackAnswer(
            etag=etag, body=SignedOrgGroupPack(**pack.model_dump(), org_id=pack.org_id, signed_pack=signed)
        )

    @staticmethod
    def _absent_etag(binding: str) -> str:
        """A cache validator for "no pack", bound like a pack's. Not a signature, hence `usedforsecurity=False`."""
        digest = hashlib.md5(f'no-pack|{binding}'.encode(), usedforsecurity=False)
        return f'"{digest.hexdigest()}"'
