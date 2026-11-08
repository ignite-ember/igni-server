"""What an extension upload reports, and what a download of one of its assets answers with.

`Publication` says whether an upload is held for a super admin and why;
`AssetDownload` decides a download's status and headers; and
`ExtensionDistribution` refuses an extension a producer does not get
exactly as it refuses one that does not exist. Each is asked directly,
with its stores substituted; the routes are exercised end to end in
`test_an_extension_ships_its_assets.py` and its neighbours.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.base_v1.schema.extensions import Published
from app.db.sql.models.extension_asset import ExtensionAsset
from app.db.sql.models.service_extension import ServiceExtension
from app.services.identity.asset_download import AssetDownload, RangeNotSatisfiable
from app.services.identity.extension_distribution import (
    AssetNotDistributed,
    ExtensionDistribution,
    ExtensionNotDistributed,
)
from app.services.identity.extension_publication import Publication
from app.services.identity.grants_comparison import AddedGrant, GrantKind, GrantsComparison
from app.services.portal.group_extensions import Entitlement

_SHA = 'a' * 64
_OTHER_SHA = 'b' * 64


def _stored(**overrides: object) -> ServiceExtension:
    fields: dict[str, object] = {
        'client_id': 'svc',
        'extension_id': 'acme.review',
        'version': '1.1.0',
        'sha256': 'c' * 64,
        'manifest': {},
    }
    return ServiceExtension(**{**fields, **overrides})


class TestWhyAnUploadIsHeld:
    def test_a_live_upload_gives_no_reason_and_adds_nothing(self):
        published = Published.of(Publication(extension=_stored()))

        assert (published.held, published.reason, published.added) == (False, None, [])

    def test_a_held_upload_says_what_it_asks_for(self):
        grant = AddedGrant(kind=GrantKind.WORKSPACE, item='read')
        comparison = GrantsComparison(wider=True, added=[grant])

        published = Published.of(Publication(extension=_stored(), held=True, comparison=comparison))

        assert published.held is True
        assert published.reason == f'held for a super admin: {comparison.reason}'
        assert published.added == [grant]

    @pytest.mark.parametrize('comparison', [None, GrantsComparison(wider=False)])
    def test_a_held_upload_with_nothing_to_compare_still_says_why(self, comparison):
        """Was `None`, or `held for a super admin: None`, when there was no
        wider comparison to quote; a service logging the reason now gets a
        sentence either way."""
        publication = Publication(extension=_stored(), held=True, comparison=comparison)

        assert publication.reason == 'held for a super admin: it asks for more than the approved version'
        assert publication.added == []


def _asset(size: int = 100) -> ExtensionAsset:
    return ExtensionAsset(client_id='svc', sha256=_SHA, size=size)


class TestWhatADownloadAnswers:
    def test_the_whole_asset(self):
        download = AssetDownload.of(_asset(), None)

        assert download.status_code == 200
        assert download.headers() == {
            'X-Igni-Asset-Sha256': _SHA,
            'ETag': f'"{_SHA}"',
            'Accept-Ranges': 'bytes',
            'Content-Length': '100',
        }
        assert (download.sending.start, download.sending.end) == (0, 99)

    def test_one_range_of_it(self):
        download = AssetDownload.of(_asset(), 'bytes=10-19')

        assert download.status_code == 206
        assert download.headers()['Content-Length'] == '10'
        assert download.headers()['Content-Range'] == 'bytes 10-19/100'

    def test_a_range_covering_all_of_it_is_the_whole_asset(self):
        download = AssetDownload.of(_asset(), 'bytes=0-')

        assert download.status_code == 200
        assert 'Content-Range' not in download.headers()

    def test_a_range_past_the_end_has_nothing_to_send(self):
        download = AssetDownload.of(_asset(), 'bytes=100-')

        assert not download.satisfiable
        assert download.status_code == 416
        assert download.headers()['Content-Range'] == 'bytes */100'
        assert 'Content-Length' not in download.headers()
        with pytest.raises(RangeNotSatisfiable):
            download.sending  # noqa: B018 — the property is what refuses


class _Entitlements:
    def __init__(self, *allowed: str) -> None:
        self._allowed = allowed

    async def of_producer(self, caller: object) -> Entitlement:
        return Entitlement(enabled={extension_id: ['g-1'] for extension_id in self._allowed})


class _Store:
    def __init__(self, found: ServiceExtension | None) -> None:
        self._found = found

    async def archive_of(self, extension_id: str) -> ServiceExtension | None:
        return self._found if self._found is not None and self._found.extension_id == extension_id else None


class _Assets:
    def __init__(self, held: ExtensionAsset | None) -> None:
        self._held = held

    async def held(self, client_id: str, sha256: str) -> ExtensionAsset | None:
        return self._held if self._held is not None and self._held.sha256 == sha256 else None


_DECLARING = {
    'assets': [
        {
            'name': 'indexer',
            'platform': 'any',
            'file': 'indexer.bin',
            'size': 100,
            'sha256': _SHA,
            'source': 'https://example.com/indexer.bin',
        }
    ]
}
_CALLER = SimpleNamespace(kind='person', id='u-1')


def _distribution(*, allowed: tuple[str, ...] = ('acme.review',), held: ExtensionAsset | None = None):
    return ExtensionDistribution(
        entitlements=_Entitlements(*allowed),  # type: ignore[arg-type]
        store=_Store(_stored(manifest=_DECLARING)),  # type: ignore[arg-type]
        priority=SimpleNamespace(),  # type: ignore[arg-type]
        assets=_Assets(held),  # type: ignore[arg-type]
    )


class TestWhatAMachineMayFetch:
    async def test_an_extension_it_gets_hands_over_its_bundle(self):
        found = await _distribution().bundle(extension_id='acme.review', caller=_CALLER)  # type: ignore[arg-type]

        assert found.extension_id == 'acme.review'

    @pytest.mark.parametrize(('allowed', 'asked'), [((), 'acme.review'), (('acme.review',), 'acme.unknown')])
    async def test_one_it_does_not_get_is_refused_like_one_that_does_not_exist(self, allowed, asked):
        with pytest.raises(ExtensionNotDistributed) as refused:
            await _distribution(allowed=allowed).bundle(extension_id=asked, caller=_CALLER)  # type: ignore[arg-type]

        assert str(refused.value) == 'No approved service ships this extension'

    async def test_an_asset_its_manifest_declares_and_igni_holds_is_downloaded(self):
        download = await _distribution(held=_asset()).asset(
            extension_id='acme.review',
            sha256=_SHA,
            caller=_CALLER,  # type: ignore[arg-type]
            byte_range='bytes=0-9',
        )

        assert (download.status_code, download.headers()['Content-Range']) == (206, 'bytes 0-9/100')

    @pytest.mark.parametrize(
        ('allowed', 'sha256', 'held', 'reason'),
        [
            ((), _SHA, _asset(), 'No distributed extension declares this asset'),
            (('acme.review',), _OTHER_SHA, _asset(), 'No distributed extension declares this asset'),
            (('acme.review',), _SHA, None, 'igni does not hold this asset'),
        ],
    )
    async def test_an_asset_is_refused_unless_all_three_hold(self, allowed, sha256, held, reason):
        with pytest.raises(AssetNotDistributed) as refused:
            await _distribution(allowed=allowed, held=held).asset(
                extension_id='acme.review',
                sha256=sha256,
                caller=_CALLER,  # type: ignore[arg-type]
                byte_range=None,
            )

        assert str(refused.value) == reason
