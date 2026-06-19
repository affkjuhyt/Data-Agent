"""The single capability registry for model-service.

Each capability (chat / embed / rerank) is served by whichever registered backend
is `available()` first. Remote backends are registered before local ones, so a
configured REMOTE_*_URL wins; otherwise the local in-process model serves. Routers
import `provider` and call `provider.active_chat()` / `active_embed()` / `active_rerank()`.
"""
from .local import LocalChatBackend, LocalEmbedBackend, LocalRerankBackend
from .registry import Registry
from .remote import RemoteChatBackend, RemoteEmbedBackend, RemoteRerankBackend

provider = Registry()
# Registration order = preference. Remote first (used when its URL is configured),
# local in-process model as the fallback.
provider.register(
    chat=RemoteChatBackend(),
    embed=RemoteEmbedBackend(),
    rerank=RemoteRerankBackend(),
)
provider.register(
    chat=LocalChatBackend(),
    embed=LocalEmbedBackend(),
    rerank=LocalRerankBackend(),
)

__all__ = ["provider", "Registry"]
