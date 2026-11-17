"""把 ReviewResult 渲染成一页纸 Markdown 报告 + JSON 台账。"""

from __future__ import annotations

import json
from pathlib import Path

from .checklist import HIGH, LOW, MEDIUM, group_results, missing_items
from .reviewer import ReviewResult

STATUS_ICON = {"present": "✅", "missing": "❌"}

SUMMARY_FIELDS = [
    ("contract_type", "合同类型"),
    ("our_party", "我方主体"),
    ("counterparty", "对方主体"),
    ("subject", "合同内容"),
    ("amount", "合同金额"),
    ("payment_terms", "付款方式"),
    ("delivery_terms", "交付安排"),
    ("term", "合同期限"),
]


def _cell(value: str, limit: int = 160) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "｜").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or "—"


def _quote_block(quote: str) -> str:
    quote = (quote or "").strip()
    if not quote:
        return ""
    return f"  > 原文：「{quote}」\n"


def _finding_block(index: int, f) -> str:
    """一条风险一行：编号. [级别] 风险｜原文｜解释。

    行政明确要求这个三段式：风险是什么、合同原文怎么写的、为什么有风险。
    整条条款缺失时没有原文可引，明确写「合同未约定」而不是留空。
    """
    quote = (f.quote or "").strip()
    quote_part = f"原文：「{quote}」" if quote else "原文：（合同未约定，无对应原文）"
    issue = (f.issue or "").strip() or "—"
    clause = f"（{f.clause}）" if f.clause else ""
    return f"**{index}. [{f.level_label}] {f.title}**{clause}｜{quote_part}｜解释：{issue}\n"


def render_markdown(result: ReviewResult) -> str:
    lines: list[str] = []
    lines.append(f"# 合同预审报告：{result.path.name}")
    lines.append("")
    mode_label = "规则清单 + 模型语义审查" if result.mode == "llm" else "仅规则清单"
    lines.append(f"> 审查时间：{result.reviewed_at} ｜ 模式：{mode_label} ｜ 综合风险：**{result.overall}**")
    lines.append(">")
    lines.append("> 本报告由 AI 通读全文自由审查生成（不受固定清单限制），每条风险给出「风险-原文-解释」三段依据，供业务与法务参考，不构成法律意见；签约前请法务确认。")
    for note in result.notes:
        lines.append(">")
        lines.append(f"> ⚠️ {note}")
    lines.append("")

    # 一、梗概
    lines.append("## 一、合同梗概")
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("| --- | --- |")
    for key, label in SUMMARY_FIELDS:
        lines.append(f"| {label} | {_cell(result.summary.get(key, ''))} |")
    lines.append("")
    if result.summary.get("tax_included"):
        lines.append(f"- 含税情况：{_cell(result.summary['tax_included'])}")

    # 二、风险清单
    lines.append("## 二、风险清单")
    lines.append("")
    lines.append(f"共 {len(result.findings)} 条：高 {len(result.high)} ｜ 中 {len(result.medium)} ｜ 低 {len(result.low)}")
    lines.append("")
    for level, label in ((HIGH, "高风险（必须改）"), (MEDIUM, "中风险（建议改）"), (LOW, "低风险（可选优化）")):
        # 同级别里先列带原文的语义风险，规则层的「缺少某条款」排后面
        bucket = sorted(
            [f for f in result.findings if f.level == level],
            key=lambda f: 1 if (f.missing or not (f.quote or "").strip()) else 0,
        )
        if not bucket:
            continue
        lines.append(f"### {label}：{len(bucket)} 条")
        lines.append("")
        for i, f in enumerate(bucket, 1):
            lines.append(_finding_block(i, f))

    advice = [f for f in result.findings if (f.suggestion or "").strip() and f.level in (HIGH, MEDIUM)]
    if advice:
        lines.append("### 修改建议")
        lines.append("")
        for f in advice[:10]:
            lines.append(f"- **{f.title}** → {f.suggestion}")
        lines.append("")

    # 三、检查清单
    present = sum(1 for c in result.checks if not c.missing)
    total = len(result.checks)
    lines.append("## 三、检查清单")
    lines.append("")
    lines.append(f"共 {total} 项，已覆盖 {present} 项，缺失 {total - present} 项。")
    if result.supplement:
        lines.append("")
        lines.append("> 补充协议只约定改动部分，上表「缺失」项应先核对主协议是否已有约定，不单独视为风险，也未计入上方风险清单。")
    lines.append("")
    grouped = group_results(result.checks)
    for group, items in grouped.items():
        if not items:
            continue
        hit = sum(1 for c in items if not c.missing)
        lines.append(f"**{group}**（{hit}/{len(items)}）")
        lines.append("")
        for c in items:
            icon = STATUS_ICON.get(c.status, "?")
            if c.missing:
                lines.append(f"- {icon} {c.item.title} — 建议：{c.item.suggestion}")
            else:
                lines.append(f"- {icon} {c.item.title}")
        lines.append("")

    # 四、缺失条款汇总
    missing = [c for c in missing_items(result.checks) if c.item.risk in (HIGH, MEDIUM)]
    lines.append("## 四、缺失/待补条款")
    lines.append("")
    if result.supplement:
        lines.append("本协议为补充/变更类协议，未约定事项依主协议执行；上表「缺失」项应在主协议中核对，故不在此重复列示。")
    elif not missing:
        lines.append("清单内条款均已覆盖。")
    else:
        for c in sorted(missing, key=lambda x: (x.item.risk != HIGH, x.item.group)):
            tag = "高" if c.item.risk == HIGH else "中"
            lines.append(f"- [{tag}] **{c.item.title}**：{c.item.suggestion}")
    lines.append("")

    # 五、必问问题
    lines.append("## 五、签约前必问对方的问题")
    lines.append("")
    if result.questions:
        for i, q in enumerate(result.questions, 1):
            lines.append(f"{i}. {q}")
    else:
        lines.append("暂无。")
    lines.append("")

    # 六、说明
    lines.append("## 六、说明")
    lines.append("")
    if result.llm_error:
        lines.append(f"- 本次未执行模型语义审查：{result.llm_error}。")
    if result.meta.get("char_count"):
        lines.append(f"- 解析字符数：{result.meta['char_count']}。")
    lines.append("- 金额、比例、账户、日期等关键信息请对照合同原文逐一复核。")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


LEVEL_COLOR = {HIGH: "C00000", MEDIUM: "BF8F00", LOW: "548235"}


def _font_setup(doc) -> None:
    """统一中文字体（Word 里默认西文字体会让中文看着别扭）。"""
    from docx.oxml.ns import qn
    from docx.shared import Pt

    for name in ("Normal", "Heading 1", "Heading 2", "Title", "List Bullet", "List Number"):
        try:
            style = doc.styles[name]
        except KeyError:
            continue
        style.font.name = "微软雅黑"
        if name == "Normal":
            style.font.size = Pt(10.5)
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            from docx.oxml import OxmlElement

            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), "微软雅黑")


def write_docx(result: ReviewResult, path: Path) -> Path:
    """渲染成可直接发给法务/领导的 Word 报告。"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    doc = Document()
    _font_setup(doc)

    title = doc.add_heading(f"合同预审报告：{result.path.name}", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    mode_label = "规则清单 + 模型语义审查" if result.mode == "llm" else "仅规则清单"
    meta = doc.add_paragraph()
    meta.add_run(
        f"审查时间：{result.reviewed_at}　模式：{mode_label}　综合风险：{result.overall}"
    ).bold = True
    note = doc.add_paragraph(
        "本报告由 AI 通读全文自由审查生成（不受固定清单限制），每条风险给出「风险-原文-解释」三段依据，"
        "供业务与法务参考，不构成法律意见；签约前请法务确认。"
    )
    note.runs[0].font.size = Pt(9)
    for extra in result.notes:
        warn = doc.add_paragraph(f"⚠️ {extra}")
        warn.runs[0].font.size = Pt(9)

    doc.add_heading("一、合同梗概", level=1)
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for key, label in SUMMARY_FIELDS:
        cells = table.add_row().cells
        cells[0].text = label
        cells[1].text = _cell(result.summary.get(key, ""), limit=300)
    doc.add_paragraph()

    doc.add_heading("二、风险清单", level=1)
    doc.add_paragraph(
        f"共 {len(result.findings)} 条：高 {len(result.high)} ｜ 中 {len(result.medium)} ｜ 低 {len(result.low)}"
    )
    for level, label in ((HIGH, "高风险（必须改）"), (MEDIUM, "中风险（建议改）"), (LOW, "低风险（可选优化）")):
        # 同级别里先列带原文的语义风险，规则层的「缺少某条款」排后面
        bucket = sorted(
            [f for f in result.findings if f.level == level],
            key=lambda f: 1 if (f.missing or not (f.quote or "").strip()) else 0,
        )
        if not bucket:
            continue
        doc.add_heading(f"{label}：{len(bucket)} 条", level=2)
        for i, f in enumerate(bucket, 1):
            p = doc.add_paragraph()
            run = p.add_run(f"{i}. {f.title}")
            run.bold = True
            run.font.color.rgb = RGBColor.from_string(LEVEL_COLOR[level])
            if f.clause:
                p.add_run(f"（{f.clause}）")
            quote = (f.quote or "").strip()
            quote_part = f"原文：「{quote}」" if quote else "原文：（合同未约定，无对应原文）"
            p.add_run(f"｜{quote_part}｜解释：{(f.issue or '').strip() or '—'}")

    doc.add_heading("三、检查清单", level=1)
    present = sum(1 for c in result.checks if not c.missing)
    total = len(result.checks)
    doc.add_paragraph(f"共 {total} 项，已覆盖 {present} 项，缺失 {total - present} 项。")
    if result.supplement:
        doc.add_paragraph(
            "补充协议只约定改动部分，上表「缺失」项应先核对主协议是否已有约定，不单独视为风险，"
            "也未计入上方风险清单。"
        )
    for group, items in group_results(result.checks).items():
        if not items:
            continue
        hit = sum(1 for c in items if not c.missing)
        doc.add_heading(f"{group}（{hit}/{len(items)}）", level=2)
        for c in items:
            icon = STATUS_ICON.get(c.status, "?")
            text = f"{icon} {c.item.title}"
            if c.missing:
                text += f" — 建议：{c.item.suggestion}"
            doc.add_paragraph(text)

    doc.add_heading("四、缺失/待补条款", level=1)
    missing = [c for c in missing_items(result.checks) if c.item.risk in (HIGH, MEDIUM)]
    if result.supplement:
        doc.add_paragraph(
            "本协议为补充/变更类协议，未约定事项依主协议执行；上表「缺失」项应在主协议中核对，故不在此重复列示。"
        )
    elif not missing:
        doc.add_paragraph("清单内条款均已覆盖。")
    else:
        for c in sorted(missing, key=lambda x: (x.item.risk != HIGH, x.item.group)):
            tag = "高" if c.item.risk == HIGH else "中"
            doc.add_paragraph(f"[{tag}] {c.item.title}：{c.item.suggestion}", style="List Bullet")

    doc.add_heading("五、签约前必问对方的问题", level=1)
    if result.questions:
        for q in result.questions:
            doc.add_paragraph(q, style="List Number")
    else:
        doc.add_paragraph("暂无。")

    doc.add_heading("六、说明", level=1)
    if result.llm_error:
        doc.add_paragraph(f"本次未执行模型语义审查：{result.llm_error}。", style="List Bullet")
    if result.meta.get("char_count"):
        doc.add_paragraph(f"解析字符数：{result.meta['char_count']}。", style="List Bullet")
    doc.add_paragraph("金额、比例、账户、日期等关键信息请对照合同原文逐一复核。", style="List Bullet")

    doc.save(str(path))
    return path


def write_report(result: ReviewResult, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = result.path.stem
    md_path = out / f"{stem}-review.md"
    json_path = out / f"{stem}-review.json"
    docx_path = out / f"{stem}-review.docx"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    paths: dict[str, Path] = {"markdown": md_path, "json": json_path}
    try:
        write_docx(result, docx_path)
        paths["docx"] = docx_path
    except Exception as exc:  # python-docx 缺失或渲染失败不影响 md/json
        paths["docx_error"] = str(exc)  # type: ignore[assignment]
    return paths
