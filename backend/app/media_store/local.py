# -*- coding: utf-8 -*-
"""本地文件夹媒体适配器：evidencePath → runtime/ 下的证明材料文件夹。

evidencePath 形如 ``libraries/company/证明材料/资质认证/1/``（相对 runtime/ 的
文件夹路径，一个序号一个文件夹、N 个文件）。本实现枚举文件夹内文件：
- 有图片 → 直接作为可插图资产；
- 只有 PDF → 用 pypdfium2 渲染首页为 PNG（缓存到 runtime/evidence_preview/）；
- 无法解析/坏文件 → 降级为 attachment（只出附件占位，不报错）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .base import KIND_ATTACHMENT, KIND_IMAGE, MediaAsset

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
PDF_EXTS = {".pdf"}

DEFAULT_WIDTH_CM = 14.0  # 页面可用宽度约 14cm（A4 减去左右边距）


class LocalFolderStore:
    """从本地 runtime 目录读取证据图文。"""

    def __init__(self, runtime_root: Path):
        self.root = Path(runtime_root)
        self.preview_dir = self.root / "evidence_preview"

    def fetch(self, ref: dict[str, Any]) -> MediaAsset | None:
        rel = str(ref.get("evidencePath") or "").strip()
        if not rel:
            return None

        folder = Path(rel).resolve() if Path(rel).is_absolute() else (self.root / rel).resolve()
        files = self._list_files(folder)
        if not files:
            return None

        name = str(ref.get("name") or "").strip() or (folder.name or "证明材料")
        images = [p for p in files if p.suffix.lower() in IMAGE_EXTS]
        pdfs = [p for p in files if p.suffix.lower() in PDF_EXTS]

        if images:
            return MediaAsset(
                asset_id=rel, kind=KIND_IMAGE, local_path=str(images[0]),
                caption=name, page_count=len(files), width_cm=DEFAULT_WIDTH_CM,
                source_path=str(images[0]),
            )

        if pdfs:
            png = self._render_pdf_first_page(pdfs[0], rel)
            if png and png.exists():
                return MediaAsset(
                    asset_id=rel, kind=KIND_IMAGE, local_path=str(png),
                    caption=name, page_count=len(pdfs), width_cm=DEFAULT_WIDTH_CM,
                    source_path=str(pdfs[0]),
                )
            # PDF 无法渲染（坏文件/占位文件）→ 降级为附件
            return MediaAsset(
                asset_id=rel, kind=KIND_ATTACHMENT, local_path="", caption=name,
                page_count=len(pdfs), width_cm=0.0, source_path=str(pdfs[0]),
            )

        # 其它类型文件（docx/xlsx/txt...）按附件处理
        return MediaAsset(
            asset_id=rel, kind=KIND_ATTACHMENT, local_path="", caption=name,
            page_count=len(files), width_cm=0.0, source_path=str(files[0]),
        )

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _list_files(folder: Path) -> list[Path]:
        if folder.is_file():
            return [folder]
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.iterdir() if p.is_file())

    def _render_pdf_first_page(self, pdf_path: Path, key: str) -> Path | None:
        """用 pypdfium2 把 PDF 首页渲染成 PNG，缓存到 preview 目录。"""
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        out = self.preview_dir / f"{digest}.png"
        if out.exists() and out.stat().st_size > 0:
            return out

        try:
            import pypdfium2 as pdfium
        except Exception:
            return None

        self.preview_dir.mkdir(parents=True, exist_ok=True)
        try:
            pdf = pdfium.PdfDocument(str(pdf_path))
            if len(pdf) < 1:
                pdf.close()
                return None
            page = pdf[0]
            bitmap = page.render(scale=2.0)  # ~144 dpi
            image = bitmap.to_pil()
            image.save(str(out), "PNG")
            bitmap.close()
            pdf.close()
            return out
        except Exception:
            # 坏文件/空占位 PDF：静默降级，交由调用方回退为附件占位
            return None
