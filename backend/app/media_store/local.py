# -*- coding: utf-8 -*-
"""本地文件夹媒体适配器：evidencePath → runtime/ 下的证明材料文件夹。

evidencePath 形如 ``libraries/company/证明材料/资质认证/1/``（相对 runtime/ 的
文件夹路径，一个序号一个文件夹、N 个文件）。本实现把文件夹内容解析为可插图资产：

- 图片（png/jpg/jpeg/webp/bmp/gif）→ 直接作为可插图资产（多张全部插入）；
- PDF → 用 pypdfium2 渲染前 N 页为 PNG（缓存到 runtime/evidence_preview/）；
- DOCX → 解包 ``word/media/*`` 提取内嵌截图/图片（写回缓存目录），并抽取标题与
  首段文字作为图注摘要；纯文字 docx 无内嵌图时按附件处理；
- 占位目录（只有 ``.keep`` 或说明性占位 ``.txt``）→ 标记 placeholder，不插图；
- 无法解析/坏文件 → 降级为 attachment（只出附件占位，不报错）。
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any

from .base import KIND_ATTACHMENT, KIND_IMAGE, MediaAsset

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}

DEFAULT_WIDTH_CM = 14.0  # 页面可用宽度约 14cm（A4 减去左右边距）
MAX_PDF_PAGES = 4        # 单份 PDF 最多渲染页数
MAX_TOTAL_PAGES = 8      # 一个 evidencePath 最多插入页数（避免一个 docx 插 63 图）

# 占位 txt 的判定：内容里出现这些标记即视为"证据尚未补齐"
_PLACEHOLDER_MARKS = ("占位文件", "尚未补齐", "证据扫描件尚未", "需要补齐")


class LocalFolderStore:
    """从本地 runtime 目录读取证据图文。"""

    def __init__(self, runtime_root: Path):
        self.root = Path(runtime_root)
        self.preview_dir = self.root / "evidence_preview"
        self._docx_media_dir = self.root / "evidence_preview" / "docx_media"

    def fetch(self, ref: dict[str, Any]) -> MediaAsset | None:
        rel = str(ref.get("evidencePath") or "").strip()
        if not rel:
            return None

        folder = Path(rel).resolve() if Path(rel).is_absolute() else (self.root / rel).resolve()
        files = self._list_files(folder)
        if not files:
            return None

        name = str(ref.get("name") or "").strip() or (folder.name or "证明材料")

        # 占位目录：只有 .keep 或占位 txt，没有真实证据。
        if self._is_placeholder_only(files):
            return MediaAsset(
                asset_id=rel, kind=KIND_ATTACHMENT, local_path="",
                caption=name, page_count=0, width_cm=0.0,
                source_path=str(files[0]) if files else "", placeholder=True,
            )

        page_images = self._render_pages(files, rel)
        if page_images:
            return MediaAsset(
                asset_id=rel, kind=KIND_IMAGE, local_path=str(page_images[0]),
                caption=name, page_count=len(files), width_cm=DEFAULT_WIDTH_CM,
                source_path=str(page_images[0]),
                page_images=[str(p) for p in page_images],
                text_summary=self._docx_text_summary(files),
            )

        pdfs = [p for p in files if p.suffix.lower() in PDF_EXTS]
        if pdfs:
            return MediaAsset(
                asset_id=rel, kind=KIND_ATTACHMENT, local_path="", caption=name,
                page_count=len(files), width_cm=0.0, source_path=str(pdfs[0]),
            )

        # 其它类型文件（xlsx/txt/纯文字 docx...）按附件处理
        return MediaAsset(
            asset_id=rel, kind=KIND_ATTACHMENT, local_path="", caption=name,
            page_count=len(files), width_cm=0.0, source_path=str(files[0]),
        )

    # ------------------------------------------------------------------ 渲染
    def _render_pages(self, files: list[Path], key: str) -> list[Path]:
        """把文件夹内可成像的文件统一渲染成有序 PNG 列表。"""

        pages: list[Path] = []
        for file in files:
            if len(pages) >= MAX_TOTAL_PAGES:
                break
            suffix = file.suffix.lower()
            if suffix in IMAGE_EXTS:
                pages.append(file)
            elif suffix in PDF_EXTS:
                pages.extend(self._render_pdf_pages(file, key)[:MAX_TOTAL_PAGES - len(pages)])
            elif suffix in DOCX_EXTS:
                pages.extend(self._extract_docx_media(file)[:MAX_TOTAL_PAGES - len(pages)])
        return pages

    def _render_pdf_pages(self, pdf_path: Path, key: str) -> list[Path]:
        """渲染 PDF 前 N 页为 PNG，缓存到 preview 目录。"""

        try:
            import pypdfium2 as pdfium
        except Exception:
            return []

        digest = hashlib.sha1((key + pdf_path.name).encode("utf-8")).hexdigest()[:16]
        outputs: list[Path] = []
        try:
            pdf = pdfium.PdfDocument(str(pdf_path))
            count = min(len(pdf), MAX_PDF_PAGES)
            for index in range(count):
                out = self.preview_dir / f"{digest}_p{index + 1}.png"
                if out.exists() and out.stat().st_size > 0:
                    outputs.append(out)
                    continue
                self.preview_dir.mkdir(parents=True, exist_ok=True)
                try:
                    page = pdf[index]
                    bitmap = page.render(scale=2.0)  # ~144 dpi
                    image = bitmap.to_pil()
                    image.save(str(out), "PNG")
                    bitmap.close()
                    page.close()
                    outputs.append(out)
                except Exception:
                    continue
            pdf.close()
        except Exception:
            # 坏文件/空占位 PDF：静默降级
            return []
        return outputs

    def _extract_docx_media(self, docx_path: Path) -> list[Path]:
        """解包 docx 的 word/media/* 内嵌图，写回缓存目录后返回 PNG/JPEG 路径。"""

        try:
            with zipfile.ZipFile(str(docx_path)) as zf:
                media_names = sorted(
                    name for name in zf.namelist()
                    if name.startswith("word/media/") and Path(name).suffix.lower() in IMAGE_EXTS
                )
                if not media_names:
                    return []
                digest = hashlib.sha1(str(docx_path.resolve()).encode("utf-8")).hexdigest()[:16]
                out_dir = self._docx_media_dir / digest
                out_dir.mkdir(parents=True, exist_ok=True)
                outputs: list[Path] = []
                for index, media_name in enumerate(media_names):
                    suffix = Path(media_name).suffix.lower()
                    out = out_dir / f"{index + 1}{suffix}"
                    if not (out.exists() and out.stat().st_size > 0):
                        data = zf.read(media_name)
                        out.write_bytes(data)
                    outputs.append(out)
                return outputs
        except Exception:
            return []

    def _docx_text_summary(self, files: list[Path]) -> str:
        """抽取 docx 的标题 + 首段作为图注摘要（供插入时说明证据内容）。"""

        for file in files:
            if file.suffix.lower() not in DOCX_EXTS:
                continue
            try:
                from docx import Document
                doc = Document(str(file))
                paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
                if paras:
                    return paras[0][:80]
            except Exception:
                return ""
        return ""

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _list_files(folder: Path) -> list[Path]:
        if folder.is_file():
            return [folder]
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.iterdir() if p.is_file())

    @staticmethod
    def _is_placeholder_only(files: list[Path]) -> bool:
        """判断文件夹是否只有占位文件（.keep / 占位说明 txt）。"""

        if not files:
            return True
        for file in files:
            # 注意：Path(".keep").suffix 是空串（隐藏文件无扩展名），必须按名字判断。
            if file.name == ".keep":
                continue
            suffix = file.suffix.lower()
            if suffix == ".txt":
                try:
                    text = file.read_text(encoding="utf-8", errors="replace")[:500]
                except OSError:
                    return False
                compact = re.sub(r"\s+", "", text)
                if any(mark in compact for mark in _PLACEHOLDER_MARKS):
                    continue
            return False
        return True
