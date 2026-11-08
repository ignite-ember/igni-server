"""The refusal of a manifest impact cannot read."""

from __future__ import annotations


class ImpactRefused(ValueError):
    """A manifest whose contributions cannot be read; the message says why."""


__all__ = ['ImpactRefused']
