"""One partial edit of a portal-added backend: what it becomes, and what that writes.

An edit names some fields and implies others. A new auth method drops the
old method's secret and settings unless the edit gives them — switching
an Azure node from its client secret to its managed identity should not
leave the secret stored, nor be refused for still carrying it. Clearing
the current method's secret without naming a method falls back to the
method what remains means. A Vertex node's address follows its project
and region. `NodeEdit` works all of that out before anything is written,
and refuses an edit that would leave a backend the router cannot use.
"""

from __future__ import annotations

from app.db.sql.schemas.inference_node import NodeChanges
from app.services.ai.backend import InferenceBackend
from app.services.ai.providers.auth import METHODS
from app.services.ai.providers.kinds import BackendKind

#: The edit fields that are also the backend's own.
_BACKEND_FIELDS = frozenset(InferenceBackend.model_fields) & frozenset(NodeChanges.model_fields)


class NodeEdit:
    """*changes* applied to *current*, with what they imply."""

    def __init__(self, current: InferenceBackend, changes: NodeChanges) -> None:
        self._current = current
        self._changes = changes

    def result(self) -> tuple[InferenceBackend, NodeChanges]:
        """The backend after the edit, and the full set of changes to write.

        Raises `BackendInvalid` if the router could not use the result.
        """
        written = self._changes.model_copy(update=self._implied().model_dump(exclude_unset=True))
        edited = InferenceBackend.validated(**self._current.model_copy(update=self._overrides(written)).model_dump())
        derived = NodeChanges()
        if written.base_url is None and edited.base_url != self._current.base_url:
            derived.base_url = edited.base_url
        if written.auth is None and edited.auth != self._current.auth:
            derived.auth = edited.auth
        return edited, written.model_copy(update=derived.model_dump(exclude_unset=True))

    def _implied(self) -> NodeChanges:
        """What a change of method clears: the old method's secret and its settings."""
        changes, current = self._changes, self._current
        implied = NodeChanges()
        if changes.auth is None or changes.auth == current.auth:
            return implied
        spec = METHODS[current.kind].get(changes.auth)
        if spec is None:
            return implied
        if spec.secret != 'api_key' and changes.api_key is None and current.api_key:
            implied.api_key = ''
        if spec.secret != 'credential' and changes.credential is None and current.credential:
            implied.credential = ''
        if changes.auth_settings is None and current.auth_settings:
            implied.auth_settings = {}
        return implied

    def _overrides(self, written: NodeChanges) -> dict[str, object]:
        """The backend fields *written* sets; an empty string clears a key, a credential or a region."""
        overrides: dict[str, object] = {
            name: (None if value == '' else value)
            for name, value in written.model_dump(exclude_none=True).items()
            if name in _BACKEND_FIELDS
        }
        if written.auth is None and self._clears_the_current_secret(written):
            # No method named: what remains says which it is now (`default_method`).
            overrides['auth'] = None
        moved = written.region is not None or written.project is not None
        if self._current.kind is BackendKind.VERTEX_OPENAI and moved and written.base_url is None:
            overrides['base_url'] = ''
        return overrides

    def _clears_the_current_secret(self, written: NodeChanges) -> bool:
        spec = METHODS[self._current.kind].get(self._current.auth) if self._current.auth else None
        if spec is None or spec.secret is None:
            return False
        cleared = written.api_key if spec.secret == 'api_key' else written.credential
        return cleared == ''
