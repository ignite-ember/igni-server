"""Why the organisation store refuses. The caller decides what HTTP makes of each."""

from __future__ import annotations


class SlugTaken(Exception):
    """Another organisation already has this slug. The message names it, so a form can point at the field."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"The name '{slug}' is already taken. Pick a different one.")
        self.slug = slug
