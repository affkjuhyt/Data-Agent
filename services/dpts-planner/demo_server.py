from __future__ import annotations

import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from basemodel.services_databaseconnector.shared_model import RetryConfig
from dpts import LLM, MockLLM, PlanningProcess, StaticMemoryEstimator
from services.database_connector.model_connector import client as model_client

MODEL_SERVICE_URL = os.environ.get("MODEL_SERVICE_URL", "")      # empty -> MockLLM
BRANCHING = int(os.environ.get("DPTS_BRANCHING", "2"))
QUEUE_SIZE = int(os.environ.get("DPTS_QUEUE_SIZE", "2"))          # 1 exploitation + 1 exploration
MAX_PLAN_DEPTH = int(os.environ.get("DPTS_MAX_PLAN_DEPTH", "5"))  # safety net; LLM decides terminal
H_ES = float(os.environ.get("DPTS_H_ES", "0.4"))
H_DS = float(os.environ.get("DPTS_H_DS", "0.6"))

app = FastAPI(title="dpts-planner-demo")
_sessions: dict[str, PlanningProcess] = {}
_HERE = os.path.dirname(os.path.abspath(__file__))


@app.on_event("startup")
async def _startup():
    if MODEL_SERVICE_URL:
        model_client.set_url(MODEL_SERVICE_URL)
        try:
            await model_client.open(RetryConfig(count=3))
        except Exception:  # noqa: BLE001
            pass


def _make_process(on_layer):
    llm = LLM(branching=BRANCHING, max_tokens=360) if MODEL_SERVICE_URL \
        else MockLLM(plan_depth=MAX_PLAN_DEPTH, branching=BRANCHING)
    return PlanningProcess(
        llm, h_es=H_ES, h_ds=H_DS, branching=BRANCHING,
        estimator=StaticMemoryEstimator(QUEUE_SIZE),
        max_layers=8, max_plan_depth=MAX_PLAN_DEPTH, on_layer=on_layer,
    )


class RunReq(BaseModel):
    user_input: str


class FailReq(BaseModel):
    run_id: str
    node_id: str


@app.get("/")
def index():
    return FileResponse(os.path.join(_HERE, "demo", "index.html"))


@app.get("/health")
def health():
    return {"status": "ok", "llm": bool(MODEL_SERVICE_URL), "model_service": MODEL_SERVICE_URL or "mock"}


# user query -> Planning Process -> first terminal node -> Plan
@app.post("/run")
async def run(req: RunReq):
    snaps: list[dict] = []
    process = _make_process(lambda s: snaps.append(s))
    await process.seed(req.user_input)
    snaps.insert(0, process.snapshot())
    plan = await process.run_until_terminal()
    run_id = uuid.uuid4().hex[:8]
    _sessions[run_id] = process
    return {"run_id": run_id, "snapshots": snaps, "plan": plan.model_dump() if plan else None, "llm": bool(MODEL_SERVICE_URL)}


# simulated broken node -> Planner gate -> trim + delete candidates -> expand again
@app.post("/fail")
async def fail(req: FailReq):
    process = _sessions.get(req.run_id)
    if process is None:
        raise HTTPException(404, "unknown run_id")
    snaps: list[dict] = []
    process.on_layer = lambda s: snaps.append(s)
    try:
        result = process.report_broken_node(req.node_id)
    except KeyError:
        raise HTTPException(400, "unknown node_id")
    snaps.insert(0, process.snapshot())
    new_plan = await process.resume()
    return {"snapshots": snaps, "plan": new_plan.model_dump() if new_plan else None,
            "cleared": result["trimmed"], "parent_id": result["parent_id"]}
