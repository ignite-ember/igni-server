"""Which email addresses may become an account here.

igni is sold to companies and its sharing is built on that: ``domain``
visibility means "my colleagues", and it works out who those are by
comparing the part of an address after the ``@``. That comparison is
only meaningful while an address domain names an employer. The moment
two people can sign up at ``gmail.com``, sharing a document with "my
domain" shares it with every stranger who did the same.

So the line is drawn once, at the door. Everything downstream — domain
visibility, the mention directory, anything later that groups people
by where they work — inherits it without having to restate it, and
without each of them keeping its own list to fall out of step with.

**Refused at sign-up, not at sign-in.** Somebody who already has an
account keeps it: a deployment that turned this on after the fact
would otherwise lock out the people it had already admitted, which is
a worse failure than having admitted them. The check is in
:meth:`UserDB.create`, which every path that makes an account goes
through.

An operator can turn it off with ``block_public_mail_domains=false``.
A deployment that does is choosing to have ``domain`` visibility mean
nothing useful, which is a legitimate choice for a single-person
install and a mistake for anybody else.
"""

from __future__ import annotations

from app.config import settings
from app.db.sql.schemas.account_errors import PersonalAddress

#: The normalised domain part of an account address.
#:
#: Lived on the `SharedArtifact` model while documents were served
#: here, because the publish and read paths both derived a domain from
#: an address and had to agree. Documents moved to `igni__artifacts`
#: and took that pair with them; this is now the only consumer in this
#: repository, so the helper comes to it rather than leaving a model
#: behind for one function.


def email_domain(email: str | None) -> str:
    """The domain part, lowercased and stripped, or empty.

    Empty for anything that is not one address: no `@`, or more than
    one. Both directions matter — this decides who counts as a
    colleague, and a bare word read as a domain is the mistake that
    grants rather than denies.
    """
    text = (email or '').strip().casefold()
    _, _, domain = text.rpartition('@')
    return domain if domain and '@' not in domain else ''


#: Domains anybody can sign up to, which therefore name no employer.
#:
#: Deliberately a list of the large consumer providers rather than an
#: attempt at completeness. Every such list is incomplete, and the
#: failure when one is missing is that a domain gets treated as a
#: company — the same state the whole deployment is in with this
#: turned off, for one provider rather than all of them.
#:
#: Subdomains count: ``@mail.yahoo.com`` is Yahoo. Matched by suffix
#: for that reason, not by equality.
PUBLIC_MAIL_DOMAINS: frozenset[str] = frozenset(
    {
        'aol.com',
        'fastmail.com',
        'gmail.com',
        'gmx.com',
        'gmx.de',
        'googlemail.com',
        'hotmail.co.uk',
        'hotmail.com',
        'hotmail.fr',
        'icloud.com',
        'inbox.com',
        'live.com',
        'mail.com',
        'mail.ru',
        'me.com',
        'msn.com',
        'outlook.com',
        'pm.me',
        'proton.me',
        'protonmail.ch',
        'protonmail.com',
        'qq.com',
        'tutanota.com',
        'web.de',
        'yahoo.co.uk',
        'yahoo.com',
        'yandex.com',
        'yandex.ru',
        'ymail.com',
        'zoho.com',
    }
)


def is_public_mail_domain(domain: str) -> bool:
    """Whether this domain is a mail provider's rather than a company's.

    Suffix matching, so ``mail.yahoo.com`` is Yahoo — but on a label
    boundary, so ``notgmail.com`` and ``mygmail.com`` are not Google.
    A plain ``endswith`` would refuse a real company whose name
    happens to end in one of these.
    """
    value = (domain or '').strip().casefold().strip('.')
    if not value:
        return False
    return any(value == known or value.endswith(f'.{known}') for known in PUBLIC_MAIL_DOMAINS)


def is_company_address(email: str) -> bool:
    """Whether an address names somewhere somebody works.

    A string with no ``@`` in it names nothing. ``email_domain``
    answers "nonsense" for ``nonsense`` — reasonable for its own job,
    where the input is known to be an address and the interesting case
    is a missing local part — but here it would report a bare word as
    a company, which is the direction that grants access.
    """
    if '@' not in (email or ''):
        return False
    domain = email_domain(email)
    return bool(domain) and not is_public_mail_domain(domain)


def refuse_public_mail(email: str) -> None:
    """Raise :class:`PersonalAddress` unless this address may open an account here."""
    if not settings.block_public_mail_domains:
        return
    if is_public_mail_domain(email_domain(email)):
        raise PersonalAddress()
