from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request

from .config import DifyConfig


class DifyError(RuntimeError):
    """A safe, user-facing error from the Dify API adapter."""


class DifyClient:
    def __init__(self, config: DifyConfig | None = None) -> None:
        self.config = config or DifyConfig.from_env()

    def _require_ready(self, workflow_type: str) -> str:
        if not self.config.enabled:
            raise DifyError("云端智能体未启用，请配置 DIFY_ENABLED=true")
        if not self.config.api_key:
            raise DifyError("未配置 DIFY_API_KEY")
        workflow_key = self.config.workflow_key(workflow_type)
        if not workflow_key:
            raise DifyError(f"未配置 {workflow_type} 工作流密钥")
        return workflow_key

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        request_obj = request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with request.urlopen(request_obj, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise DifyError(f"Dify API 返回 HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise DifyError(f"Dify API 网络请求失败: {exc.reason}") from exc
        except TimeoutError as exc:
            raise DifyError("Dify API 请求超时") from exc

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DifyError("Dify API 返回了非法 JSON") from exc
        if not isinstance(decoded, dict):
            raise DifyError("Dify API 返回格式不是 JSON 对象")
        return decoded

    def upload_file(self, path: Path, *, user: str) -> dict[str, Any]:
        if not path.is_file():
            raise DifyError("待上传文件不存在")
        boundary = f"----tender-agent-{uuid.uuid4().hex}"
        filename = path.name
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        content = path.read_bytes()
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        user_part = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="user"\r\n\r\n'
            f"{user}\r\n"
        ).encode("utf-8")
        suffix = f"--{boundary}--\r\n".encode("utf-8")
        body = prefix + content + b"\r\n" + user_part + suffix
        return self._request(
            "POST",
            "/files/upload",
            body=body,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
        )

    def run_workflow(
        self,
        workflow_type: str,
        *,
        inputs: dict[str, Any],
        user: str,
    ) -> dict[str, Any]:
        workflow_key = self._require_ready(workflow_type)
        payload = {
            "inputs": inputs,
            "response_mode": "blocking",
            "user": user,
        }
        response = self._request(
            "POST",
            "/workflows/run",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {workflow_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        data = response.get("data")
        if isinstance(data, dict) and str(data.get("status") or "").lower() in {"failed", "stopped"}:
            detail = str(data.get("error") or response.get("message") or "工作流执行失败")
            raise DifyError(f"Dify 工作流执行失败: {detail}")
        return response

    def parse_tender(
        self,
        path: Path,
        *,
        workspace_id: str,
        project_id: str,
        project_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        upload = self.upload_file(path, user=workspace_id or "tender-agent")
        upload_id = str(upload.get("id") or upload.get("upload_file_id") or "").strip()
        if not upload_id:
            raise DifyError("Dify 文件上传成功但未返回文件 ID")
        return self.run_workflow(
            "parse",
            inputs={
                "tender_file": {
                    "type": "document",
                    "transfer_method": "local_file",
                    "upload_file_id": upload_id,
                },
                "workspace_id": workspace_id,
                "project_id": project_id,
                "project_context": project_context or {"source": "local_upload"},
            },
            user=workspace_id or "tender-agent",
        )

    def draft_section(
        self,
        *,
        workspace_id: str,
        project_id: str,
        section: dict[str, Any],
        fine_review: dict[str, Any],
        project: dict[str, Any],
        company: dict[str, Any],
        knowledge: dict[str, Any],
        mode: str = "open",
    ) -> dict[str, Any]:
        return self.run_workflow(
            "section",
            inputs={
                "workspace_id": workspace_id,
                "project_id": project_id,
                "mode": mode,
                "section": section,
                "fine_review": fine_review,
                "project": project,
                "company": company,
                "knowledge": knowledge,
            },
            user=workspace_id or "tender-agent",
        )
