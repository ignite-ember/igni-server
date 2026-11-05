"""The journey fixture's extension process: one read-only tool over MCP."""

from __future__ import annotations

from igni_extension import IgniExtension, ToolHints

ext = IgniExtension('journey.fixture', instructions='The free-base journey fixture. `ping` says it is here.')

READS = ToolHints(read_only=True, destructive=False, idempotent=True, open_world=False)


@ext.tool(hints=READS)
async def ping() -> str:
    """Say the journey's extension is installed and running."""
    return 'journey fixture: here'


def main() -> None:
    ext.run()
