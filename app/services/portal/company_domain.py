"""An email domain an organisation may own: what makes one valid, and how it is stored."""

from __future__ import annotations

import re

from app.security.account_domains import is_public_mail_domain
from app.services.portal.org_errors import InvalidDomain, PublicMailDomain


class CompanyDomain:
    """The one rule for a domain an organisation claims.

    Stored lowercased, with no ``@`` and no surrounding dots. A consumer
    mail domain is refused: ``gmail.com`` names no employer, and claiming
    it would make every Gmail user a member.
    """

    #: A hostname with at least one dot: labels of letters, digits and
    #: hyphens, no hyphen at either end of a label.
    PATTERN = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')

    @classmethod
    def checked(cls, value: str) -> str:
        """The domain as stored. `InvalidDomain`, `PublicMailDomain`."""
        domain = value.strip().casefold().lstrip('@').strip('.')
        if not cls.PATTERN.match(domain):
            raise InvalidDomain(value)
        if is_public_mail_domain(domain):
            raise PublicMailDomain(domain)
        return domain
