"""An organisation's slug: what makes one valid, and how one is made from a name."""

from __future__ import annotations

import re
import uuid

from app.services.portal.org_errors import InvalidSlug


class OrgSlug:
    """The one slug rule, applied to slugs typed and slugs derived alike.

    3-32 characters, lowercase alphanumeric and hyphens, no hyphen at
    either end. Named once because two paths produce a slug and only one
    of them used to check it.
    """

    PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$')

    @classmethod
    def checked(cls, slug: str) -> str:
        """A slug the caller supplied, lowercased. Rejects, never repairs: `InvalidSlug`."""
        slug = slug.lower().strip()
        if not cls.PATTERN.match(slug):
            raise InvalidSlug()
        return slug

    @classmethod
    def derived(cls, display_name: str) -> str:
        """Make a slug from a display name. Always returns a valid one.

        The derivation never used to go through the check, so the
        3-32-character rule held only for slugs somebody typed. What it
        produced instead, for names that are not unreasonable:

            '日本語'  -> ''     every character is non-alphanumeric
            '!!!'    -> ''
            'Ab'     -> 'ab'   two characters, under the minimum

        `organizations.slug` is NOT NULL and UNIQUE, so an empty string
        stored fine — and the *second* org whose name derived to empty
        collided with the first and was told its slug was taken.

        A name that cannot produce a slug gets a generated one rather than
        a 422: refusing "日本語" as an organisation name is the wrong
        answer, and the slug is editable afterwards. `strip('-')` runs
        again after the truncation, because cutting at 32 can land on a
        hyphen and leave one trailing.
        """
        slug = re.sub(r'[^a-z0-9]+', '-', display_name.lower()).strip('-')[:32].strip('-')
        if cls.PATTERN.match(slug):
            return slug
        # 16 characters, so it satisfies the pattern by construction.
        return f'org-{uuid.uuid4().hex[:12]}'

    @classmethod
    def for_new_org(cls, *, slug: str | None, display_name: str) -> str:
        """The supplied slug checked, or one derived from the name when none was given."""
        return cls.checked(slug) if slug else cls.derived(display_name)
