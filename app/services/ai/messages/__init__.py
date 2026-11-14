"""`POST /v1/messages`: Anthropic's Messages API, proxied to wherever Claude is served.

Anthropic's own API (or a compatible gateway), Bedrock, Vertex AI and
Microsoft Foundry each take the same Messages body with small
differences of address, version and signing (`calls.py`); Bedrock alone
answers a stream in AWS's binary event stream, which is turned back into
Anthropic's server-sent events (`upstream.py`). The caller sees one API
whichever of them serves the model.

Deliberately empty of re-exports, for the reason `app/services/ai`'s own
package docstring gives.
"""
