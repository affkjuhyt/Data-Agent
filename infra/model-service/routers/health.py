from fastapi import APIRouter
from models import is_ready

import remote

router = APIRouter()


@router.get("/health")
def health() -> dict:
    # `status` tracks only local embed/rerank readiness (what the container
    # healthcheck and kb-backend wait on). The LLM gateway is a remote, optional
    # dependency, so its state is reported separately and never fails liveness.
    return {"status": "ok" if is_ready() else "loading", "llm": remote.llm_status()}
