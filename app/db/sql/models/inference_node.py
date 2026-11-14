"""InferenceNode — a GPU node an admin added from the portal.

The third source of backends, alongside the static JSON file and the
self-registering runtime registry. It exists because the other two both
require somebody outside the product: the file needs a redeploy, and
registration needs cluster automation holding a token. Neither is
available to an admin who has just been handed a machine's address.

## Why this is a table and not another expiring key

Registered nodes live in Redis with a heartbeat TTL, because a node that
stops checking in is a node that has gone away and routing to it would
fail every request. That is right for hardware that announces itself.

It is wrong here. Nothing heartbeats on behalf of a node somebody typed
into a form, so a TTL would make it vanish minutes after it was added —
which reads as the product losing the entry rather than as a liveness
signal. So the row is permanent, and reachability is answered separately
by probing the node. An unreachable pinned node stays on the page and
turns red.

The credential is encrypted under ``SECRET_ENCRYPTION_KEY``, like the
deployment's own signing key (F150). It was stored as given, on the
argument that encrypting it would need a key with the same exposure as
the database. That key already exists and is not in the database — so a
backup, a snapshot or a read-only SQL session, which are exactly the
copies that travel, carried a working credential for a GPU node, and
nothing else in the shared plane did.
"""

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import Boolean, Column, ForeignKey, func, Index, Integer, Text, text, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class InferenceNode(Base):
    __tablename__ = 'inference_nodes'
    __table_args__ = (
        Index('idx_inference_nodes_org', 'org_id'),
        # Ids namespace Redis keys for the concurrency gauge, so two
        # nodes sharing one would each appear half as loaded as they are.
        UniqueConstraint('node_id', name='uq_inference_nodes_node_id'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))

    # The routing identifier, matching what a registered node would send
    # as ``id``. Distinct from the surrogate primary key so an admin can
    # rename nothing and the key stays stable.
    node_id: Mapped[str] = Column(Text, nullable=False)

    # Provenance, not ownership — see ``InferenceNodeDB.list_all``: the
    # fleet belongs to the deployment, and ``org_id`` records who added a
    # node rather than who may use it.
    #
    # ``SET NULL`` and nullable for exactly that reason. This was ``NOT
    # NULL`` with ``ON DELETE CASCADE``, so deleting an organisation
    # deleted the GPU nodes it had registered — capacity every other
    # organisation was routing to, removed by an admin retiring a defunct
    # org. Verified against a real database before it was changed, and
    # reachable through ``DELETE /v1/portal/organizations/{org_id}``.
    org_id: Mapped[str | None] = Column(Text, ForeignKey('organizations.id', ondelete='SET NULL'), nullable=True)

    base_url: Mapped[str] = Column(Text, nullable=False)
    # Every id this node answers to: the base model, plus one entry per
    # LoRA adapter it was started with. JSONB because a model id is
    # arbitrary text and an adapter can be named anything, commas
    # included.
    models: Mapped[Any] = Column(JSONB, nullable=False)
    #: The node's proxy key, Fernet-encrypted (``encrypt_secret``);
    #: ``InferenceNodeDB`` encrypts on write and decrypts only to route.
    #: Never returned by the API. `info['encrypted']` puts it in
    #: `tools/rotate_secret_key.py`'s reach.
    api_key: Mapped[str | None] = Column(Text, nullable=True, info={'encrypted': True})
    #: Who serves the node (``BackendKind``): ``openai`` for the customer's
    #: own and anything OpenAI-compatible, or a cloud's kind.
    kind: Mapped[str] = Column(Text, nullable=False, server_default=text("'openai'"))
    #: Vertex AI only: where the model runs.
    region: Mapped[str | None] = Column(Text, nullable=True)
    project: Mapped[str | None] = Column(Text, nullable=True)
    #: How requests to the node are authenticated (``AuthMethod``), and
    #: the method's settings — client ids, a service account's email —
    #: which are not secret. Always named: migration 0030 backfilled
    #: every row that was there before.
    auth: Mapped[str] = Column(Text, nullable=False)
    auth_settings: Mapped[Any] = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    #: A cloud node's stored credential (a service-account key, a client
    #: secret), Fernet-encrypted like ``api_key`` and never returned.
    #: Null means the server's own cloud identity.
    credential: Mapped[str | None] = Column(Text, nullable=True, info={'encrypted': True})
    #: The provider's own name for each model the node serves.
    model_ids: Mapped[Any] = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    max_concurrent: Mapped[int] = Column(Integer, nullable=False, server_default=text('64'))
    multimodal: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('false'))

    enabled: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('true'))

    # ``SET NULL`` and nullable: this records who made the row, and the
    # row has to outlive them. With ``NO ACTION`` and ``NOT NULL``,
    # deleting an account raised a foreign-key violation the moment its
    # owner had ever created anything — a 500 on "delete my account" for
    # anybody who had done admin work and then left an org that survives.
    # CASCADE would have been worse: an admin closing their account would
    # take the organisation's policy with them.
    created_by_user_id: Mapped[str | None] = Column(
        Text, ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True
    )
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
