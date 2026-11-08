"""What became of an extension upload: live, or held for a super admin."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.db.sql.models.service_extension import ServiceExtension
from app.services.identity.grants_comparison import AddedGrant, GrantsComparison


class Publication(BaseModel):
    """What became of an upload."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    extension: ServiceExtension
    #: Held for a super admin: it asks for more than the approved version.
    held: bool = False
    #: Against the approved version; `None` when there was nothing to compare with.
    comparison: GrantsComparison | None = None

    @property
    def reason(self) -> str | None:
        """Why it is held, in a line a service can log; `None` when it is not held."""
        if not self.held:
            return None
        why = self.comparison.reason if self.comparison is not None else None
        return f'held for a super admin: {why or "it asks for more than the approved version"}'

    @property
    def added(self) -> list[AddedGrant]:
        """What a held version asks for that the approved one did not."""
        return list(self.comparison.added) if self.held and self.comparison is not None else []
