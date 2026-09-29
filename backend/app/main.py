from contextlib import asynccontextmanager
import asyncio
import logging
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .api.bid_writing import create_router as create_bid_writing_router
from .api.cloud_agent import create_router as create_cloud_agent_router
from .api.config import create_router as create_config_router
from .api.materials import create_router as create_materials_router
from .api.monitoring import create_router as create_monitoring_router
from .api.notifications import create_router as create_notifications_router
from .api.ontology import create_router as create_ontology_router
from .api.wecom import create_router as create_wecom_router
from .store import Store
from .cloud_agent.run_service import CloudRunService
from .tender_monitor.service import search_catalog
from .tender_monitor.scheduler import monitoring_loop


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT / "frontend" / "dist"
store = Store(ROOT)
cloud_run_service = CloudRunService(ROOT)

logger = logging.getLogger(__name__)


def run_pi_preflight() -> None:
    """启动时自检 Pi 模型可用性；任何失败只记日志，绝不影响服务启动。"""

    try:
        from .pi_agent.preflight import get_report

        report = get_report(refresh=True)
        if report.get("ok"):
            logger.info("Pi 模型自检通过：%s", report.get("summary"))
        else:
            logger.warning("Pi 模型自检发现问题：%s", report.get("summary"))
            for problem in report.get("problems") or []:
                logger.warning("  - %s", problem)
        for warning in report.get("warnings") or []:
            logger.warning("Pi 模型自检提示：%s", warning)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pi 模型自检未能完成：%s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.initialize()
    # 自检会读文件并查询网关：放进后台线程，绝不阻塞服务启动。
    threading.Thread(target=run_pi_preflight, name="pi-model-preflight", daemon=True).start()
    monitoring_task = asyncio.create_task(monitoring_loop(store))
    try:
        yield
    finally:
        monitoring_task.cancel()
        with suppress(asyncio.CancelledError):
            await monitoring_task


app = FastAPI(
    title="招投标智能体 API",
    version="2.0.0",
    description="Tender intelligence workspace API.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(create_config_router(store))
app.include_router(create_materials_router(store))
app.include_router(create_monitoring_router(store))
app.include_router(create_notifications_router(store))
app.include_router(create_ontology_router(store))
app.include_router(create_wecom_router(store))
app.include_router(create_bid_writing_router(store))
app.include_router(create_cloud_agent_router(cloud_run_service, store))


class SearchPayload(BaseModel):
    query: str = ""
    region: str = ""
    type: str = ""
    currentOnly: bool = False


class AnalyzePayload(BaseModel):
    projectId: str


class DraftPayload(BaseModel):
    draft: str = ""


class FineReviewStatusPayload(BaseModel):
    rowId: str = Field(min_length=1)
    status: str = Field(min_length=1)
    reviewer: str = "当前用户"
    uncertaintyReason: str = ""
    responseValue: str | None = None
    materialBindings: list[dict[str, Any]] | None = None


class AskPayload(BaseModel):
    question: str = Field(min_length=1)
    projectId: str = ""


class ConfigPayload(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class ReminderPayload(BaseModel):
    projectId: str
    label: str = ""
    remindAt: str = ""


class ImportPayload(BaseModel):
    name: str = ""
    text: str = ""
    mimeType: str = ""
    fileBase64: str = ""
    documentKind: str = "notice"


class LibraryPayload(BaseModel):
    name: str = ""
    text: str = ""
    mimeType: str = ""
    fileBase64: str = ""


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "tender-agent-api"}


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    return store.bootstrap()


@app.get("/api/catalog")
def catalog(
    query: str = "",
    region: str = "",
    type: str = "",
    currentOnly: bool = False,
) -> list[dict[str, Any]]:
    return store.filtered_catalog(query=query, region=region, type=type, current_only=currentOnly)


@app.get("/api/catalog/{project_id}")
def catalog_detail(project_id: str) -> dict[str, Any]:
    item = store.get_project(project_id)
    if not item:
        raise HTTPException(status_code=404, detail="未找到项目")
    return item


@app.post("/api/search/realtime")
def realtime_search(payload: SearchPayload) -> dict[str, Any]:
    return search_catalog(store, payload.model_dump())


@app.post("/api/catalog/{project_id}/read")
def mark_read(project_id: str) -> dict[str, Any]:
    item = store.mark_read(project_id)
    if not item:
        raise HTTPException(status_code=404, detail="未找到项目")
    return {"item": item}


@app.post("/api/catalog/{project_id}/follow-up")
def follow_up(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    item = store.set_follow_up(project_id, payload)
    if not item:
        raise HTTPException(status_code=404, detail="未找到项目")
    return {"item": item}


@app.post("/api/catalog/{project_id}/download")
def download_project(project_id: str) -> dict[str, Any]:
    result = store.download_project(project_id)
    if not result:
        raise HTTPException(status_code=404, detail="未找到项目")
    return result


@app.post("/api/tenders/match")
def match_tenders(payload: dict[str, Any]) -> dict[str, Any]:
    return store.batch_match(payload.get("projectIds", []))


@app.post("/api/tenders/{project_id}/generate-response")
def generate_response(project_id: str) -> dict[str, Any]:
    result = store.generate_response(project_id)
    if not result:
        raise HTTPException(status_code=404, detail="未找到项目")
    return result


@app.post("/api/import")
def import_document(payload: ImportPayload) -> dict[str, Any]:
    if payload.fileBase64:
        import base64

        content = base64.b64decode(payload.fileBase64.encode("utf-8"))
        name = payload.name or "uploaded-document.txt"
        return store.import_document(name, content, payload.mimeType or "", payload.documentKind)
    text = payload.text or ""
    return store.import_document(
        payload.name or "招标文件.txt",
        text.encode("utf-8"),
        payload.mimeType or "text/plain",
        payload.documentKind,
    )


@app.get("/api/workspaces/{workspace_id}")
def workspace(workspace_id: str) -> dict[str, Any]:
    result = store.workspace(workspace_id)
    if not result:
        raise HTTPException(status_code=404, detail="未找到工作台")
    return result


@app.post("/api/workspaces/{workspace_id}/draft")
def save_draft(workspace_id: str, payload: DraftPayload) -> dict[str, Any]:
    result = store.save_draft(workspace_id, payload.draft)
    if not result:
        raise HTTPException(status_code=404, detail="未找到工作台")
    return result


@app.post("/api/workspaces/{workspace_id}/fine-review/status")
def update_fine_review_status_api(workspace_id: str, payload: FineReviewStatusPayload) -> dict[str, Any]:
    try:
        result = store.update_fine_review_row(
            workspace_id,
            payload.rowId,
            payload.status,
            payload.reviewer,
            payload.uncertaintyReason,
            payload.responseValue,
            payload.materialBindings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="未找到可更新的细匹配条款")
    return result


@app.post("/api/analyze")
def analyze(payload: AnalyzePayload) -> dict[str, Any]:
    result = store.analyze(payload.projectId)
    if not result:
        raise HTTPException(status_code=404, detail="未找到项目")
    return result


@app.post("/api/ask")
def ask(payload: AskPayload) -> dict[str, Any]:
    return store.ask(payload.question, payload.projectId)


@app.post("/api/agent/chat")
def agent_chat(payload: AskPayload) -> dict[str, Any]:
    return store.ask(payload.question, payload.projectId, agent=True)


@app.get("/api/libraries")
def libraries() -> list[dict[str, Any]]:
    return store.library_snapshot()


@app.get("/api/libraries/{library_key}")
def library_detail(library_key: str) -> dict[str, Any]:
    result = store.library_detail(library_key)
    if not result:
        raise HTTPException(status_code=404, detail="未找到资料库")
    return result


@app.post("/api/libraries/{library_key}")
def save_library(library_key: str, payload: LibraryPayload) -> dict[str, Any]:
    if payload.fileBase64:
        import base64

        content = base64.b64decode(payload.fileBase64.encode("utf-8"))
        name = payload.name or "资料文件"
        mime_type = payload.mimeType or ""
    else:
        content = (payload.text or "").encode("utf-8")
        name = payload.name or "资料文件"
        mime_type = payload.mimeType or "text/plain"
    return store.save_library_document(library_key, name, content, mime_type)


@app.delete("/api/libraries/{library_key}/{document_id}")
def delete_library(library_key: str, document_id: str) -> dict[str, Any]:
    return store.delete_library_document(library_key, document_id)


@app.get("/api/automation/status")
def automation_status() -> dict[str, Any]:
    return store.automation_status()


@app.post("/api/automation/run")
def automation_run() -> dict[str, Any]:
    return store.run_automation()


@app.get("/api/reminders")
def reminders() -> list[dict[str, Any]]:
    return store.reminder_snapshot()


@app.post("/api/reminders")
def create_reminder(payload: ReminderPayload) -> dict[str, Any]:
    return store.create_reminder(payload.model_dump())


@app.get("/runtime/{file_path:path}")
def runtime_file(file_path: str) -> FileResponse:
    target = (store.runtime_root / file_path).resolve()
    if store.runtime_root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
