"""Delivering what a service says happened to the people it concerns.

``docs/service-identity.md`` §10.1: a service says *what happened and
who it concerns*; this decides, so no service has to:

* **From whom.** The source is the extension the calling service ships,
  read from its upload — never from the request. A service with no
  extension uploaded has nothing for a notification to open, and is
  refused. A ``target.page`` must be one of that extension's pages.
* **Who.** Each recipient, a user id or an address, is resolved to an
  active member of the token's organisation. Anybody else is dropped,
  never told elsewhere. The actor is never told about their own act.
* **Once.** A person is told about an ``event_key`` once, however often
  the service retries — the table's partial unique index decides. A
  batch is processed in order after recipients are resolved to accounts,
  so the first notification that reaches a person with a key wins.

Then whoever got something new is woken.
"""

from __future__ import annotations

from app.api.base_v1.schema.notification import ServiceNotification
from app.db.sql.directory import OrgDirectoryDB
from app.db.sql.models.service_extension import ServiceExtension
from app.db.sql.notifications import NotificationsDB
from app.db.sql.schemas.notification import NotificationDraft, NotificationRef
from app.db.sql.service_extensions import ServiceExtensionDB
from app.security.service_caller import ServiceCaller
from app.services.notifications.nudge import Nudger


class NoExtension(LookupError):
    """The calling service has not uploaded the extension a notification comes from."""


class NotAPage(ValueError):
    """A ``target.page`` that is not one of the calling service's extension pages."""


class NotificationDelivery:
    def __init__(
        self,
        *,
        notifications: NotificationsDB | None = None,
        directory: OrgDirectoryDB | None = None,
        extensions: ServiceExtensionDB | None = None,
        nudger: Nudger | None = None,
    ) -> None:
        self._notifications = notifications or NotificationsDB()
        self._directory = directory or OrgDirectoryDB()
        self._extensions = extensions or ServiceExtensionDB()
        self._nudger = nudger or Nudger()

    @classmethod
    def default(cls) -> NotificationDelivery:
        """The instance a route gets from ``Depends``."""
        return cls()

    async def deliver(self, caller: ServiceCaller, notifications: list[ServiceNotification]) -> int:
        """Write every notification for the people it reaches. Answers how many rows.

        Every notification is checked before any is written, so a batch
        with one bad target writes nothing.
        """
        extension = await self._extensions.of(caller.client_id)
        if extension is None:
            raise NoExtension(
                'This service has no extension uploaded, so a notification has nowhere to come from. '
                f'Upload your extension first (PUT /v1/clients/{caller.client_id}/extension).'
            )
        pages = self.pages_of(extension)
        for notification in notifications:
            if notification.target is not None and notification.target.page not in pages:
                raise NotAPage(
                    f'target.page {notification.target.page!r} is not one of the pages of '
                    f'{extension.extension_id}: {", ".join(sorted(pages)) or "it declares none"}.'
                )
        # In order, one at a time, and that is the contract: the first
        # notification in the batch to reach a person with an
        # ``event_key`` wins, so a service sends the verbs of one event
        # most specific first (mention, then comment, then reply).
        written: list[NotificationRef] = []
        for notification in notifications:
            written.extend(await self._one(caller, extension, notification))
        if written:
            newest = max((ref.created_at for ref in written if ref.created_at), default=None)
            await self._nudger.wake({ref.user_id for ref in written}, cursor=newest.isoformat() if newest else '')
        return len(written)

    async def _one(
        self, caller: ServiceCaller, extension: ServiceExtension, notification: ServiceNotification
    ) -> list[NotificationRef]:
        actor_user_id, actor_email = await self._actor(caller.org_id, notification.actor.strip())
        identities = sorted({value.strip() for value in notification.recipients if value.strip()})
        if not identities:
            return []
        present = await self._directory.resolve(caller.org_id, identities, include_former=False)
        recipients = sorted(
            {
                str(user.user_id)
                for user, _membership in present
                if not self._is_actor(user.user_id, user.email, actor_user_id, actor_email)
            }
        )
        draft = NotificationDraft(
            source=extension.extension_id,
            source_client_id=caller.client_id,
            kind=notification.kind,
            title=notification.title,
            body=notification.body,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            target=notification.target,
            event_key=notification.event_key.strip(),
        )
        return await self._notifications.tell(org_id=caller.org_id, user_ids=recipients, draft=draft)

    async def _actor(self, org_id: str, actor: str) -> tuple[str, str]:
        """The actor as ``(user_id, address)``, filled in from the directory
        when they are or were a member; otherwise as the service named them."""
        if not actor:
            return '', ''
        found = await self._directory.resolve(org_id, [actor])
        if found:
            user, _membership = found[0]
            return str(user.user_id), str(user.email or '')
        return ('', actor) if '@' in actor else (actor, '')

    @staticmethod
    def _is_actor(user_id: str, email: str | None, actor_user_id: str, actor_email: str) -> bool:
        if actor_user_id and user_id == actor_user_id:
            return True
        return bool(actor_email and email and email.lower() == actor_email.lower())

    @staticmethod
    def pages_of(extension: ServiceExtension) -> set[str]:
        """The page kinds the extension's manifest contributes."""
        contributes = extension.manifest.get('contributes') if isinstance(extension.manifest, dict) else None
        pages = contributes.get('pages') if isinstance(contributes, dict) else None
        if not isinstance(pages, list):
            return set()
        return {str(page['kind']) for page in pages if isinstance(page, dict) and page.get('kind')}
