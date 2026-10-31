"""igni server as the identity provider for its own services.

CodeIndex, Artifacts and whatever comes after them are deployed with one
setting — this server's URL — and learn everything else from it: the
keys that sign igni's tokens (`/.well-known/jwks.json`), the endpoints
(`/.well-known/openid-configuration`), and, once a super admin has
approved them, tokens of their own. See `docs/service-identity.md`.
"""
