"""An email address as it may appear in logs and the audit trail.

`jane@example.com` becomes `j***@example.com`. An operator needs the
domain and enough to tell two records apart; they do not need the
mailbox, and logs and the audit trail are retained. One definition, used
by the erasure and subject-access endpoints and the code throttle, which
each carried a verbatim copy.
"""

from __future__ import annotations


def mask_email(email: str) -> str:
    """`jane@example.com` -> `j***@example.com`; anything without a domain -> `***`."""
    local, _, domain = email.partition('@')
    if not domain:
        return '***'
    return f'{local[:1]}***@{domain}'
