"""The producers feed's shape in `docs/service-identity.md` §11.3 is the one served.

The gateway's authors build against that block. It left `internet` out of
`profile` after profiles gained it, although §12 says the feed carries it
and the feed did: a gateway written from §11.3 would have dropped the one
field that is a security grant.
"""

from __future__ import annotations

import pathlib
import re

from app.api.base_v1.schema.effective_vm_profile import EffectiveVmProfile
from app.api.base_v1.schema.producer_feed import ProducerFeedItem

_DOC = pathlib.Path(__file__).resolve().parent.parent / 'docs' / 'service-identity.md'


def _feed_block() -> str:
    section = _DOC.read_text().split('### 11.3 ', 1)[1].split('\n### ', 1)[0]
    match = re.search(r'GET /v1/platform/producers\?.*?\n(.*?)\n```', section, re.S)
    assert match, 'no feed block in §11.3'
    return match.group(1)


def _profile_keys(block: str) -> set[str]:
    inner = re.search(r'"profile": \{(.*?)\} \| null', block, re.S)
    assert inner, 'no profile object in the §11.3 feed block'
    # `"packages": {"apt", "pip", "npm"}` names the package managers, not
    # profile keys.
    flat = re.sub(r'\{[^{}]*\}', '', inner.group(1))
    return set(re.findall(r'"(\w+)"', flat))


def _item_keys(block: str) -> set[str]:
    item = block.split('"producers": [{', 1)[1].split('}],', 1)[0]
    while re.search(r'\{[^{}]*\}', item):
        item = re.sub(r'\{[^{}]*\}', '', item)
    return set(re.findall(r'"(\w+)"', item))


def test_the_documented_profile_has_every_served_field():
    assert _profile_keys(_feed_block()) == set(EffectiveVmProfile.model_fields)


def test_the_documented_item_has_every_served_field():
    assert _item_keys(_feed_block()) == set(ProducerFeedItem.model_fields)
