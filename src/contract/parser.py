"""合同文本抽取：.docx / .pdf / .txt / .md。

- docx：python-docx（项目 Python 自带），按段落 + 表格顺序拼接。
- pdf：pypdf（vendor 目录，纯 Python）；扫描件（图片型 PDF）抽不出字时明确报错。
- 不处理旧版 .doc，提示另存为 .docx。
"""

from __future__ import annotations

import re
from pathlib import Path

SUPPORTED = {".docx", ".pdf", ".txt", ".md", ".markdown"}


class ParseError(RuntimeError):
    """文件无法解析时抛出，消息直接给用户看。"""


def extract_text(path: str | Path, settings=None) -> str:
    p = Path(path)
    if not p.exists():
        raise ParseError(f"文件不存在：{p}")
    suffix = p.suffix.lower()
    if suffix == ".docx":
        text = _from_docx(p)
    elif suffix == ".pdf":
        text = _from_pdf(p, settings)
    elif suffix in {".txt", ".md", ".markdown"}:
        text = _from_text(p)
    elif suffix == ".doc":
        raise ParseError("旧版 .doc 不支持，请用 Word 打开后「另存为 .docx」再上传")
    else:
        raise ParseError(f"不支持的文件类型：{suffix}（支持 docx / pdf / txt / md）")
    return _normalize(text)


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _from_text(p: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ParseError(f"无法识别文本编码：{p.name}，请另存为 UTF-8")


def _from_docx(p: Path) -> str:
    try:
        import docx  # type: ignore
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底
        raise ParseError("缺少 python-docx，无法解析 .docx（请安装 python-docx）") from exc
    doc = docx.Document(str(p))
    parts: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            line = " | ".join(c for c in cells if c)
            if line:
                parts.append(line)
    text = "\n".join(parts)
    if len(text) < 30:
        raise ParseError("docx 中没读到有效文字，可能是空文件或内容全在图片里")
    return text


def _from_pdf(p: Path, settings=None) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ParseError("缺少 pypdf，无法解析 .pdf（请先执行 out/install_vendor.py 安装）") from exc
    reader = PdfReader(str(p))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ParseError("PDF 已加密，无法读取。请提供解密版本") from exc
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - 单页失败不影响其它页
            pages.append("")
    text = "\n".join(pages)
    if len(re.sub(r"\s", "", text)) < 30:
        return _pdf_via_ocr(p, settings)
    return _join_soft_breaks(text)


def _join_soft_breaks(text: str) -> str:
    r"""合并 PDF 抽取产生的软换行。

    pypdf 按版面宽度硬换行，会把一句话切断：「…向甲方所\n地有管辖权的人民法院…」。
    模型只看到后半句，就会编出"争议解决地不在我方所在地"这种结论（已实测发生）。
    合并后引用才是完整句子。

    判定：上一行不是以句末标点结尾，且当前行不是新条款/新表格行，就接到上一行。
    """
    lines = text.split("\n")
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            out.append("")
            continue
        if out and out[-1] and not _ENDS_BLOCK.search(out[-1]) and not _NEW_BLOCK.match(line):
            out[-1] = out[-1] + line
        else:
            out.append(line)
    return "\n".join(out)


# 句末标点（冒号不算：冒号后通常紧跟内容，正该合并）
_ENDS_BLOCK = re.compile(r"[。；！？\.;!?\)）】」』”\"]$")
# 新条款 / 新表格行 / 新附件的开头，绝不能并到上一行
_NEW_BLOCK = re.compile(
    r"^\s*(?:"
    r"[一二三四五六七八九十]+[、\s]"
    r"|[（(][一二三四五六七八九十\d]+[）)]"
    r"|\d+[、.．)）]"
    r"|第[一二三四五六七八九十百零]+[条章节款项]"
    r"|附[件表录]"
    r"|(?:甲方|乙方|丙方|买方|卖方|供方|需方)\s*[:：]"
    r"|合[同约]编号"
    r")"
)


def _pdf_via_ocr(p: Path, settings) -> str:
    """文字型抽取失败（扫描件）时走本地 OCR；未开启则给出可操作提示。"""
    from . import ocr

    if settings is None or not getattr(settings, "contract_ocr_enabled", False):
        raise ParseError(
            "这个 PDF 是扫描件（图片），抽不出文字。请提供 Word 版或可复制文字的 PDF"
        )
    max_pages = int(getattr(settings, "contract_ocr_max_pages", 30) or 30)
    dpi = int(getattr(settings, "contract_ocr_dpi", 150) or 150)
    try:
        text = ocr.ocr_pdf(p, max_pages=max_pages, dpi=dpi)
    except ocr.OcrError as exc:
        raise ParseError(str(exc)) from exc
    if len(re.sub(r"\s", "", text)) < 30:
        raise ParseError("扫描件识别后仍无有效文字，请检查文件或提供 Word 版")
    return text


def page_hint(text: str) -> int:
    """粗略估计页数，用于报告展示。"""
    return max(1, text.count("\f") + 1)
