"""One asset on its way to a machine: whole, one `Range` of it, or a range it does not have.

A machine on a poor link resumes a several-hundred-megabyte file instead
of starting it again, so a download answers a single `Range` (see
`ByteRange`). What it answers with — the status and the headers that
describe the bytes — is decided here, so the route only streams them.
"""

from __future__ import annotations

from http import HTTPStatus

from pydantic import BaseModel, ConfigDict

from app.db.sql.models.extension_asset import ExtensionAsset
from app.services.identity.extension_assets import ByteRange


class RangeNotSatisfiable(ValueError):
    """The download's `Range` asks for nothing the asset has; there are no bytes to send."""


class AssetDownload(BaseModel):
    """A held asset and the bytes of it a download sends."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    asset: ExtensionAsset
    #: `None` when the `Range` asks for nothing the asset has (416).
    span: ByteRange | None

    @classmethod
    def of(cls, asset: ExtensionAsset, byte_range: str | None) -> AssetDownload:
        return cls(asset=asset, span=ByteRange.of(byte_range, asset.size))

    @property
    def satisfiable(self) -> bool:
        return self.span is not None

    @property
    def sending(self) -> ByteRange:
        """The bytes to send; refused when the `Range` cannot be satisfied."""
        if self.span is None:
            raise RangeNotSatisfiable(f'the asset has {self.asset.size} bytes')
        return self.span

    @property
    def status_code(self) -> int:
        if self.span is None:
            return HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE
        return HTTPStatus.PARTIAL_CONTENT if self.span.partial else HTTPStatus.OK

    def headers(self) -> dict[str, str]:
        """What says which asset this is, and which of its bytes follow."""
        size = self.asset.size
        headers = {'X-Igni-Asset-Sha256': self.asset.sha256, 'ETag': f'"{self.asset.sha256}"', 'Accept-Ranges': 'bytes'}
        if self.span is None:
            return {**headers, 'Content-Range': f'bytes */{size}'}
        headers['Content-Length'] = str(self.span.length)
        if self.span.partial:
            headers['Content-Range'] = f'bytes {self.span.start}-{self.span.end}/{size}'
        return headers
