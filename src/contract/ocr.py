"""本地 OCR：把扫描件 PDF 转成文字（pymupdf 渲染 + RapidOCR 识别）。

选用本地引擎而不是云端 OCR 的原因：合同多为保密材料，本地识别不出本机，且成本为 0。
实测（12 核 CPU、150dpi）：约 10 秒/页，16 页扫描件 160 秒。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# 实测速度，用于给用户/审批人预估等待时间
SECONDS_PER_PAGE = 10.0
_EPS = 1e-6

_engine = None
_engine_lock = threading.Lock()


class OcrError(RuntimeError):
    """OCR 不可用或失败，消息直接给用户看。"""


def available() -> tuple[bool, str]:
    """OCR 依赖是否就绪。返回 (可用, 说明)。"""
    try:
        import pymupdf  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        return False, f"缺少 OCR 依赖（pymupdf / rapidocr-onnxruntime）：{exc}"
    return True, "本地 OCR 就绪"


def page_count(path: str | Path) -> int:
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover
        return 0
    try:
        with pymupdf.open(str(path)) as doc:
            return doc.page_count
    except Exception:  # noqa: BLE001
        return 0


def _first_pages_text(path: Path, pages: int = 3) -> str:
    import pymupdf  # type: ignore

    with pymupdf.open(str(path)) as doc:
        limit = min(pages, doc.page_count)
        return "".join(doc[i].get_text() or "" for i in range(limit))


def needs_ocr(path: str | Path) -> bool:
    """PDF 是否是图片型扫描件（前几页几乎没文字）。非 PDF 一律 False。"""
    p = Path(path)
    if p.suffix.lower() != ".pdf" or not p.exists():
        return False
    try:
        text = _first_pages_text(p)
    except Exception:  # noqa: BLE001 - 打不开就交给正常解析报错
        return False
    return len("".join(text.split())) < 30


def estimate_seconds(pages: int) -> int:
    return max(1, int(round(pages * SECONDS_PER_PAGE)))


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise OcrError("缺少 rapidocr-onnxruntime，无法识别扫描件") from exc
            log.info("正在初始化本地 OCR 引擎（首次约 2 秒）")
            _engine = RapidOCR()
        return _engine


def ocr_pdf(
    path: str | Path,
    max_pages: int = 30,
    dpi: int = 150,
    tmp_dir: str | Path | None = None,
) -> str:
    """把 PDF 每页渲染成图片再识别，返回拼接后的文字。

    超过 max_pages 直接报错（避免一个 200 页扫描件把审批卡住半小时）。
    """
    import pymupdf  # type: ignore

    p = Path(path)
    total = page_count(p)
    if total <= 0:
        raise OcrError(f"PDF 打不开：{p.name}")
    if total > max_pages:
        raise OcrError(f"扫描件共 {total} 页，超过自动识别上限 {max_pages} 页，请拆分后再上传")

    engine = _get_engine()
    work = Path(tmp_dir) if tmp_dir else p.parent / f".ocr-{p.stem[:20]}"
    work.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    try:
        with pymupdf.open(str(p)) as doc:
            for i in range(doc.page_count):
                image = work / f"p{i:03d}.png"
                pix = doc[i].get_pixmap(dpi=dpi)
                image.write_bytes(pix.tobytes("png"))
                result, _ = engine(str(image), use_cls=False)
                if result:
                    chunks.append("\n".join(part[1] for part in result))
                if i % 5 == 4:
                    log.info("OCR 进度：%s/%s 页", i + 1, doc.page_count)
    finally:
        for leftover in work.glob("p*.png"):
            try:
                leftover.unlink()
            except OSError:
                pass

    text = "\n\n".join(chunks)
    if len("".join(text.split())) < 30:
        raise OcrError("扫描件里没识别出文字（可能是纯图片或空白页），请检查文件")
    log.info("OCR 完成：%s（%s 页，%s 字）", p.name, total, len(text))
    return text
