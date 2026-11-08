"""What an extension upload reports, and what a download of one of its assets answers with.

`Publication` says whether an upload is held for a super admin and why;
`AssetDownload` decides a download's status and headers; and
`ExtensionDistribution` refuses an extension a producer does not get
exactly as it refuses one that does not exist. Each is asked directly,
with its stores substituted; the routes are exercised end to end in
`test_an_extension_ships_its_assets.py` and its neighbours.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from app.api.base_v1.schema.extensions import Published
from app.db.sql.models.extension_asset import ExtensionAsset
from app.db.sql.models.registered_client import RegisteredClient
from app.db.sql.models.service_extension import ServiceExtension
from app.services.identity.asset_download import AssetDownload, RangeNotSatisfiable
from app.services.identity.extension_distribution import (
    AssetNotDistributed,
    ExtensionDistribution,
    ExtensionNotDistributed,
)
from app.services.identity.extension_publication import Publication
from app.services.identity.extensions import ServiceExtensions
from app.services.identity.grants_comparison import AddedGrant, GrantKind, GrantsComparison
from app.services.portal.group_extensions import Entitlement
from tools.export_openapi import render

_SHA = 'a' * 64
_OTHER_SHA = 'b' * 64


def _stored(**overrides: object) -> ServiceExtension:
    fields: dict[str, object] = {
        'client_id': 'svc',
        'extension_id': 'acme.review',
        'version': '1.1.0',
        'sha256': 'c' * 64,
        'manifest': {},
        'signature': 'sig',
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


class _Uploads:
    def __init__(self, *, live: ServiceExtension | None = None, held: ServiceExtension | None = None) -> None:
        self._live = live
        self._held = held

    async def held_of(self, client_id: str) -> ServiceExtension | None:
        return self._held

    async def of(self, client_id: str) -> ServiceExtension | None:
        return self._live


class TestWhatAServiceIsToldItHolds:
    """`GET /clients/{id}/extension`: the latest upload, so a restart sends only a changed bundle."""

    async def test_nothing_uploaded_is_nothing(self):
        assert await ServiceExtensions(store=_Uploads()).latest('svc') is None  # type: ignore[arg-type]

    async def test_the_live_version_when_nothing_is_held(self):
        latest = await ServiceExtensions(store=_Uploads(live=_stored())).latest('svc')  # type: ignore[arg-type]

        assert latest is not None
        assert (latest.extension.version, latest.held, latest.reason, latest.added) == ('1.1.0', False, None, [])

    async def test_the_held_version_wins_and_says_why_without_a_comparison(self):
        uploads = _Uploads(live=_stored(), held=_stored(version='2.0.0'))

        latest = await ServiceExtensions(store=uploads).latest('svc')  # type: ignore[arg-type]

        assert latest is not None
        assert (latest.extension.version, latest.held) == ('2.0.0', True)
        assert latest.reason == 'held for a super admin: it asks for more than the approved version'
        assert latest.added == []


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

    @pytest.mark.parametrize('byte_range', ['', 'items=0-5', 'bytes=0-1,5-6', 'bytes=-'])
    def test_a_range_it_does_not_read_is_the_whole_asset(self, byte_range):
        download = AssetDownload.of(_asset(), byte_range)

        assert download.status_code == 200
        assert download.headers()['Content-Length'] == '100'
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


_PUBLISHED = datetime(2026, 1, 1, tzinfo=timezone.utc)
_JWKS: dict[str, list[dict[str, str]]] = {'keys': [{'kty': 'EC', 'kid': 'k-1', 'crv': 'P-256', 'x': 'x', 'y': 'y'}]}


class _Listed:
    def __init__(self, *rows: tuple[ServiceExtension, RegisteredClient]) -> None:
        self._rows = list(rows)

    async def distributed(self) -> list[tuple[ServiceExtension, RegisteredClient]]:
        return self._rows


class _Priority:
    async def distributed(self) -> list[str]:
        return ['acme.other', 'acme.review']


class _ProducerEntitlements:
    async def of_producer(self, caller: object) -> Entitlement:
        return Entitlement(group='g-1', group_name='Reviewers', enabled={'acme.review': ['g-1']})


def _client(jwks: dict[str, object]) -> RegisteredClient:
    return RegisteredClient(client_id='svc', base_url='https://review.example.com', jwks=jwks)


class TestWhatAMachineIsListed:
    async def _listing(self, extension: ServiceExtension, jwks: dict[str, object]):
        other = _stored(client_id='other', extension_id='acme.other')
        distribution = ExtensionDistribution(
            entitlements=_ProducerEntitlements(),  # type: ignore[arg-type]
            store=_Listed((extension, _client(jwks)), (other, _client(jwks))),  # type: ignore[arg-type]
            priority=_Priority(),  # type: ignore[arg-type]
            assets=SimpleNamespace(),  # type: ignore[arg-type]
        )
        return await distribution.listing(_CALLER)  # type: ignore[arg-type]

    async def test_only_what_the_producer_gets_is_listed_and_ranked(self):
        listed = await self._listing(_stored(manifest={'name': 'Review'}, published_at=_PUBLISHED), _JWKS)

        assert [e.extension_id for e in listed.extensions] == ['acme.review']
        assert listed.priority == ['acme.review']
        assert (listed.group, listed.group_name) == ('g-1', 'Reviewers')
        assert (listed.extensions[0].groups, listed.extensions[0].apps) == (['g-1'], [])

    async def test_a_manifest_with_no_name_is_listed_by_its_id(self):
        listed = await self._listing(_stored(published_at=_PUBLISHED), _JWKS)

        assert (listed.extensions[0].name, listed.extensions[0].description) == ('acme.review', '')

    async def test_a_manifest_todays_rules_refuse_is_listed_as_stored(self):
        """Was read as stored; validating it again would let one old row
        fail every producer's listing."""
        stored = {'id': '', 'name': 'Review', 'description': 'Reviews code', 'vm': 'not a vm'}

        listed = await self._listing(_stored(manifest=stored, published_at=_PUBLISHED), _JWKS)

        assert (listed.extensions[0].name, listed.extensions[0].description) == ('Review', 'Reviews code')

    async def test_the_publisher_keys_are_handed_back_as_stored(self):
        """Not parsed again: a member today's `PublicJwk` would refuse is
        echoed rather than failing the listing, and nothing is added."""
        stored = {'keys': [{**_JWKS['keys'][0], 'x5t': 'legacy'}], 'note': 'kept'}

        listed = await self._listing(_stored(published_at=_PUBLISHED), stored)

        assert listed.model_dump(mode='json')['extensions'][0]['publisher_jwks'] == stored


class TestWhatTheSchemaPublishes:
    """The contract the SDK is generated from, as the app serves it."""

    def _schemas(self) -> dict[str, dict[str, object]]:
        return json.loads(render())['components']['schemas']

    def test_the_publisher_keys_are_a_jwk_set_of_public_keys(self):
        """Was a free-form object; an SDK now gets the keys typed."""
        schemas = self._schemas()

        assert schemas['DistributedExtension']['properties']['publisher_jwks'] == {  # type: ignore[index]
            '$ref': '#/components/schemas/JwkSet'
        }
        assert schemas['JwkSet']['properties']['keys']['items'] == {  # type: ignore[index]
            '$ref': '#/components/schemas/PublicJwk'
        }
        assert (schemas['PublicJwk']['additionalProperties'], schemas['PublicJwk']['required']) == (False, ['kty'])

    def test_what_an_upload_added_is_published_with_no_default(self):
        """An empty list per response rather than one shared default; the
        published schema no longer states `"default": []`."""
        added = self._schemas()['Published']['properties']['added']  # type: ignore[index]

        assert 'default' not in added
        assert added['type'] == 'array'
