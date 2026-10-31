"""How a rendered message actually leaves the building.

Templates, retries and the "what if there is no relay" policy all live
above this line. A transport takes a finished ``EmailMessage`` and gets
it to the provider — nothing else.

The split exists because Amazon SES can be reached two entirely
different ways, and a customer's choice between them is about credential
management rather than about email:

* **SMTP** works everywhere, including against SES, and needs a username
  and password sitting in configuration.
* **The SES API** authenticates with the AWS credential chain, so a
  deployment running on EC2, ECS or EKS uses its instance or pod role
  and stores no long-lived secret at all. That is the reason to have it.

Both return ``bool`` rather than raising. Callers are background tasks
where an exception is invisible, and "did it send?" is the only question
any of them asks.
"""

from abc import ABC, abstractmethod
from email.message import EmailMessage


class EmailTransport(ABC):
    """One way of handing a message to a mail provider."""

    @abstractmethod
    async def send(self, message: EmailMessage) -> bool:
        """Deliver ``message``. False on any failure, never raises.

        Implementations log the reason before returning False. Swallowing
        the detail here would leave an operator with nothing, and raising
        would take down a background task that has no handler.
        """

    @property
    @abstractmethod
    def description(self) -> str:
        """A short phrase naming where mail is going, for startup logs.

        "SES API in eu-west-1" and "SMTP to email-smtp.eu-west-1…" are
        the difference between an operator knowing which set of
        credentials to check and guessing.
        """
