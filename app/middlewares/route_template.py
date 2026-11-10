"""The full route template of a request, mount prefix included."""

from collections.abc import Mapping


def full_route_template(request_path: str, route_path: str, path_params: Mapping[str, object] | None) -> str:
    """The route template including whatever prefix it was mounted under.

    `scope['route'].path` is the path *relative to its own router*, so a
    portal endpoint arrives as `/groups` — no `/portal`, no `/v1` — which
    is ambiguous (several routers have a `/groups`).

    Render the relative template with the request's own path parameters,
    find that rendering as a suffix of the real request path, and keep
    everything before it as the prefix. If the suffix does not match, the
    real path is returned rather than a guess.
    """
    rendered = route_path
    for name, value in (path_params or {}).items():
        if value is not None:
            rendered = rendered.replace('{' + name + '}', str(value))

    if rendered and request_path.endswith(rendered):
        prefix = request_path[: -len(rendered)]
        return f'{prefix}{route_path}'
    return request_path
