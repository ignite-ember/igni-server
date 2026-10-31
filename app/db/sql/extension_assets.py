"""Storing extension assets. See `app.db.sql.models.extension_asset`.

The bytes live in a Postgres large object and move in chunks both ways:
`lo_put` at an offset on the way in, `lo_get` of a range on the way out.
Neither the upload nor the download holds the file. The server-side
functions rather than a driver API because asyncpg has none, and these
need no descriptor kept open — each call names the object and the offset.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.exc import IntegrityError

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.extension_asset import ExtensionAsset
from app.utils.singleton import SingletonMeta

_CREATE = text('SELECT lo_create(0)')
_PUT = text('SELECT lo_put(:oid, :offset, :data)').bindparams(bindparam('data', type_=BYTEA))
_GET = text('SELECT lo_get(:oid, :offset, :length)')


class ExtensionAssetDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def store(self, asset: ExtensionAsset, chunks: AsyncIterator[bytes]) -> ExtensionAsset:
        """Write `chunks` into a new large object and keep `asset` pointing at it.

        One transaction: if `chunks` raises — the bytes were not the asset
        named — the object and the row are rolled back together, and an
        asset already held under this digest is untouched. A held one is
        replaced, and the trigger unlinks its old object.

        Two uploads of one digest at once both write, and the second to
        commit collides on the key; it rolls back, object and all, and
        answers with the first — the same bytes, by their digest.
        """
        try:
            async with self.session_scope() as session:
                oid = int((await session.execute(_CREATE)).scalar_one())
                offset = 0
                async for chunk in chunks:
                    await session.execute(_PUT, {'oid': oid, 'offset': offset, 'data': chunk})
                    offset += len(chunk)
                await session.execute(
                    delete(ExtensionAsset).where(
                        ExtensionAsset.client_id == asset.client_id, ExtensionAsset.sha256 == asset.sha256
                    )
                )
                asset.content_oid = oid
                session.add(asset)
        except IntegrityError:
            held = await self.get(asset.client_id, asset.sha256)
            if held is None:
                raise
            return held
        return asset

    async def read(self, oid: int, offset: int, length: int) -> bytes:
        """`length` bytes of the object from `offset`, or fewer at its end.

        A short transaction per chunk, so a download of several hundred
        megabytes does not keep a pooled connection for its whole length.
        The bytes under a digest never change, so chunks read in separate
        transactions are still one file.
        """
        async with self.session_scope() as session:
            result = await session.execute(_GET, {'oid': oid, 'offset': offset, 'length': length})
            return bytes(result.scalar_one())

    async def sizes(self, client_id: str) -> dict[str, int]:
        """What the client has uploaded: digest to size, without the bytes."""
        async with self.session_scope() as session:
            result = await session.execute(
                select(ExtensionAsset.sha256, ExtensionAsset.size).where(ExtensionAsset.client_id == client_id)
            )
            return {sha256: size for sha256, size in result.all()}

    async def get(self, client_id: str, sha256: str) -> ExtensionAsset | None:
        async with self.session_scope() as session:
            return await session.get(ExtensionAsset, (client_id, sha256))

    async def forget_all_but(self, client_id: str, keep: set[str]) -> None:
        """Drop the client's assets that no version it holds declares; the trigger unlinks their objects."""
        async with self.session_scope() as session:
            await session.execute(
                delete(ExtensionAsset).where(
                    ExtensionAsset.client_id == client_id, ExtensionAsset.sha256.not_in(keep or {''})
                )
            )
