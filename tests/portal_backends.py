"""What the portal tests of cloud backends share: the bodies they add, and the router's view of them."""

from __future__ import annotations

from app.services.ai.backend import InferenceBackend
from app.services.ai.pool_manager import PoolManager

VERTEX = {
    'node_id': 'vertex-eu',
    'kind': 'vertex_openai',
    'project': 'acme-ai',
    'region': 'europe-west4',
    'models': ['gemini-pro'],
    'model_ids': {'gemini-pro': 'google/gemini-2.5-pro'},
    'max_concurrent': 200,
}

AZURE = {
    'node_id': 'azure-eu',
    'kind': 'azure_openai',
    'base_url': 'https://acme.openai.azure.com/openai/v1',
    'models': ['gpt-5'],
}


async def routed() -> dict[str, InferenceBackend]:
    """Every backend as the router loads it, by id."""
    return {backend.id: backend for backend in await PoolManager().provider.load()}
