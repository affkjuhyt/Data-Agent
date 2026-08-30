from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth_core import startup as run_startup_tasks
from routers import api_keys, approvals, compliance, governance, health, ip_allowlist, secrets
from shared.observability import setup_observability


def create_app() -> FastAPI:
    app = FastAPI(title="auth_api", docs_url="/api/auth/docs")
    setup_observability("auth_api", app)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    app.include_router(health.router)
    app.include_router(approvals.router)
    app.include_router(ip_allowlist.router)
    app.include_router(api_keys.router)
    app.include_router(secrets.router)
    app.include_router(governance.router)
    app.include_router(compliance.router)

    @app.on_event("startup")
    async def startup() -> None:
        await run_startup_tasks()

    return app
