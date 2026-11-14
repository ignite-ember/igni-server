"""Database layer for portal-managed inference nodes."""

from datetime import datetime, timezone
import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.inference_node import InferenceNode
from app.db.sql.schemas.inference_node import NodeChanges, NodeIdAlreadyRegistered
from app.services.ai.backend import InferenceBackend
from app.services.ai.providers.auth import AuthMethod, default_method
from app.services.ai.providers.kinds import BackendKind
from app.services.secrets import decrypt_secret, encrypt_secret
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

#: The constraint a duplicate registration violates. Matched by name so
#: an unrelated driver message mentioning the column does not read as a
#: node-id conflict.
_NODE_ID_INDEX = 'uq_inference_nodes_node_id'


class InferenceNodeDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """Nodes an admin added from the portal."""

    async def list_all(self) -> list[InferenceNode]:
        """Every pinned node, across every org.

        Not org-scoped, and deliberately so: the inference fleet belongs
        to the deployment rather than to an organization within it, and
        routing has no org to scope by. ``org_id`` records who added the
        node, not who may use it.
        """
        async with self.session_scope() as session:
            rows = (await session.execute(select(InferenceNode).order_by(InferenceNode.node_id))).scalars().all()
            return list(rows)

    async def get_by_node_id(self, node_id: str) -> InferenceNode | None:
        async with self.session_scope() as session:
            return (
                (await session.execute(select(InferenceNode).where(InferenceNode.node_id == node_id))).scalars().first()
            )

    async def create(
        self,
        backend: InferenceBackend,
        *,
        # Nullable, matching the column: ``org_id`` is provenance rather
        # than ownership, so a node whose registering org has since been
        # deleted keeps working with no org recorded.
        org_id: str | None,
        created_by_user_id: str,
    ) -> InferenceNode:
        """Store *backend*, already validated, with its secrets encrypted."""
        async with self.session_scope() as session:
            node = InferenceNode(
                id=str(uuid.uuid4()),
                node_id=backend.id,
                org_id=org_id,
                base_url=backend.base_url,
                models=list(backend.models),
                api_key=encrypt_secret(backend.api_key) if backend.api_key else None,
                max_concurrent=backend.max_concurrent,
                multimodal=backend.multimodal,
                kind=backend.kind.value,
                region=backend.region,
                project=backend.project,
                credential=encrypt_secret(backend.credential) if backend.credential else None,
                model_ids=dict(backend.model_ids),
                # The method the router resolved, stored by name: clearing a
                # secret later must not quietly change what it means.
                auth=(
                    backend.auth
                    or default_method(
                        backend.kind, has_api_key=bool(backend.api_key), has_credential=bool(backend.credential)
                    )
                ).value,
                auth_settings=dict(backend.auth_settings),
                created_by_user_id=created_by_user_id,
            )
            session.add(node)
            try:
                await session.flush()
            except IntegrityError as exc:
                # The caller pre-checks ``get_by_node_id`` and raises this
                # same 409, which is a check-then-act: eight concurrent
                # registrations of one node_id gave one 201 and seven 500s.
                # Registration is driven by a node's own startup, so two
                # instances racing on the same id is the ordinary way to
                # reach it rather than an unusual one.
                # Matched on the index name, not on the word "node_id"
                # appearing anywhere in the driver's message — which
                # would also catch an unrelated error that happened to
                # mention the column. notifications.py in this package
                # matches the same way.
                if _NODE_ID_INDEX not in str(getattr(exc, 'orig', exc)):
                    raise
                raise NodeIdAlreadyRegistered(backend.id) from exc
            await session.refresh(node)
            return node

    async def update(self, node_id: str, changes: NodeChanges) -> InferenceNode | None:
        """Apply a partial update. None if the node is gone.

        Every field is named. This took ``**changes`` and ``setattr``'d
        whatever arrived, dispatching on field-name string literals, so
        a misspelled keyword set a new attribute on the ORM object
        instead of failing — an edit that silently did nothing.
        """
        async with self.session_scope() as session:
            node = (
                (await session.execute(select(InferenceNode).where(InferenceNode.node_id == node_id))).scalars().first()
            )
            if node is None:
                return None

            if changes.base_url is not None:
                node.base_url = changes.base_url
            if changes.models is not None:
                node.models = list(changes.models)
            if changes.max_concurrent is not None:
                node.max_concurrent = changes.max_concurrent
            if changes.multimodal is not None:
                node.multimodal = changes.multimodal
            if changes.enabled is not None:
                node.enabled = changes.enabled
            if changes.api_key is not None:
                # Absent leaves the credential alone; an explicit empty
                # string clears it. The portal never sends the key back,
                # so treating absent as "clear" would wipe it on every
                # edit of anything else.
                node.api_key = None if changes.clears_api_key() else encrypt_secret(changes.api_key)
            if changes.region is not None:
                node.region = changes.region or None
            if changes.project is not None:
                node.project = changes.project or None
            if changes.credential is not None:
                node.credential = None if changes.clears_credential() else encrypt_secret(changes.credential)
            if changes.model_ids is not None:
                node.model_ids = dict(changes.model_ids)
            if changes.auth is not None:
                node.auth = changes.auth.value
            if changes.auth_settings is not None:
                node.auth_settings = dict(changes.auth_settings)

            node.updated_at = datetime.now(timezone.utc)
            await session.flush()
            await session.refresh(node)
            return node

    async def delete(self, node_id: str) -> bool:
        """Remove a node. True if it was there."""
        async with self.session_scope() as session:
            node = (
                (await session.execute(select(InferenceNode).where(InferenceNode.node_id == node_id))).scalars().first()
            )
            if node is None:
                return False
            await session.delete(node)
            return True

    def _models_of(self, node: InferenceNode) -> list[str]:
        """The ids this node answers to.

        Stored as JSON because a model id is arbitrary text — an adapter can
        be named anything, commas included.
        """
        raw = node.models
        if isinstance(raw, list):
            return [str(name) for name in raw if str(name).strip()]
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError):
            return [str(raw)] if raw else []
        return [str(name) for name in parsed] if isinstance(parsed, list) else [str(parsed)]

    def to_backend(self, node: InferenceNode) -> InferenceBackend:
        """The routing model for a stored node.

        A method rather than a loose function taking the row first:
        backend_provider.py had to import the free function to convert
        rows this class had just returned.

        The stored key is a ciphertext (see ``InferenceNode.api_key``) and
        is decrypted here, the one place it is used. A key that will not
        decrypt raises ``SecretDecryptError``: routing to the node without
        it would fail every request with the node's 401 instead.
        """
        return InferenceBackend(
            id=str(node.node_id),
            base_url=str(node.base_url),
            models=self._models_of(node),
            api_key=decrypt_secret(str(node.api_key)) if node.api_key else None,
            max_concurrent=int(node.max_concurrent),
            multimodal=bool(node.multimodal),
            kind=BackendKind(node.kind),
            region=node.region,
            project=node.project,
            credential=decrypt_secret(str(node.credential)) if node.credential else None,
            model_ids=dict(node.model_ids),
            auth=AuthMethod(node.auth),
            auth_settings=dict(node.auth_settings),
        )
