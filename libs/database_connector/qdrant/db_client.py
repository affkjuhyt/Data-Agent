"""
Exposes a module-level `client` (AsyncQdrantClient) for router.py.
Initialized by app/main.py on startup.
"""
from qdrant_client.async_qdrant_client import AsyncQdrantClient

client: AsyncQdrantClient | None = None


def init(url: str) -> None:
    global client
    client = AsyncQdrantClient(url=url)
