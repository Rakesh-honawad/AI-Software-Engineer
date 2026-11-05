import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from api.routes import impact, repositories, dead_code
from entitlements.routes import router as entitlements_router
from api.routes import explain
from api.routes import repo_qa
from api.routes import investigate
from api.routes import workspace_files
# from auth.dependencies import get_current_user
from github_integration.routes import router as github_integration_router
from github_integration.repository_routes import router as github_app_repository_router
from api.routes import auth

app = FastAPI(
    title="AI Software Engineer API",
    description="Engineer Mode backend: repository indexing and impact analysis.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        [origin.strip().rstrip("/") for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
        or [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ]
    ),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(repositories.router)
app.include_router(investigate.router)
app.include_router(impact.router)
app.include_router(dead_code.router)
app.include_router(entitlements_router)
app.include_router(explain.router)
app.include_router(repo_qa.router)
app.include_router(workspace_files.router)
app.include_router(auth.router)

app.include_router(github_integration_router)
app.include_router(github_app_repository_router)

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}



from github_integration.branch_routes import router as github_branch_router
app.include_router(github_branch_router)
