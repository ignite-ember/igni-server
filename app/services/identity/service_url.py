"""A service's URL: what it is reached at, and what its tokens are for.

A person's token for a service names the service's URL as its audience
(`aud`), and the service accepts a token only when that is its own URL.
So the service needs nothing from igni to accept people — not a client
id, not a lookup — and the URL is compared as a string on both sides,
which is why there is exactly one way to write it.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from app.config import settings

_LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')


class ServiceUrl:
    @staticmethod
    def normalise(url: str, *, what: str = 'the URL') -> str:
        """`scheme://host[:port][/path]`: lower-case scheme and host, no
        trailing slash, no query, no fragment. Raises `ValueError`.

        Plain http only for this machine (`localhost`, `*.localhost`, the
        loopback addresses), or in dev: a token's audience
        is fetched over this URL, and the person's identity travels in
        it.
        """
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
            raise ValueError(f'{what} must be an absolute http(s) URL')
        if parts.query or parts.fragment:
            raise ValueError(f'{what} must not have a query or a fragment')
        scheme = parts.scheme.lower()
        local = parts.hostname in _LOCAL_HOSTS or parts.hostname.endswith('.localhost')
        if scheme == 'http' and not local and settings.app_env.lower() != 'dev':
            raise ValueError(f'{what} must use https')
        host = parts.hostname.lower()
        netloc = f'{host}:{parts.port}' if parts.port else host
        return urlunsplit((scheme, netloc, parts.path.rstrip('/'), '', ''))

    @staticmethod
    def own_origin(url: str) -> str:
        """A service's registered URL: an origin of its own, with no path.

        A service lives on its own hostname. A path under somebody
        else's would put its routes — `/v1/igni/subjects/...` among them —
        in another server's namespace, and igni's own origin is the worst
        case: igni would call itself to erase a person, and a token for
        the service would name igni. Different ports are different
        origins, which is what the local stack relies on.
        """
        normalised = ServiceUrl.normalise(url, what='client_uri')
        parts = urlsplit(normalised)
        if parts.path:
            raise ValueError('client_uri must be an origin of its own (scheme, host and port), with no path')
        if normalised == ServiceUrl.normalise(settings.public_base_url, what='PUBLIC_BASE_URL'):
            raise ValueError("client_uri must not be igni's own address; a service has its own hostname")
        return normalised

    @staticmethod
    def redirect(url: str) -> str:
        """A redirect URI: checked like a service URL, but kept exactly as
        registered — RFC 6749 compares redirect URIs character for character."""
        ServiceUrl.normalise(url, what='a redirect URI')
        return url
