from fastapi import APIRouter

from backends import registry, Capability, chat_status

router = APIRouter()


@router.get("/health")
def health() -> dict:
    # `status` tracks only local embed/rerank readiness (what the container
    # healthcheck and kb-backend wait on). The LLM gateway is a remote, optional
    # dependency, so its state is reported separately and never fails liveness.
    ready = registry.has(Capability.EMBED) and registry.has(Capability.RERANK)
    return {"status": "ok" if ready else "loading", "llm": chat_status(registry)}
