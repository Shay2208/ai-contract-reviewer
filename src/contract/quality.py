"""区分「原件质量问题」与「合同条款风险」。

背景（2026-11-18）：不受限提示词跑出来，排在最前面两条是「条款编号严重混乱」
「大量OCR乱码错字」——它们确实是真的，但性质是**这份文件本身有毛病**，
不是**合同条款对我方不利**。销售/行政要在三秒内看到"该不该盖章、要跟对方争什么"，
被这两条挡住视线就等于没有结论。

处理口径（用户 2026-11-18 选定）：降级 + 单列一节。
- 风险清单/卡片 Top/审批评论：只放真风险，原件质量问题不参与排序。
- 报告文档：单独一节「原件质量提示」，放在末尾，信息不丢。
"""

from __future__ import annotations

import re
from typing import Any

# 标题或问题描述命中这些词，判定为「原件质量问题」而非合同风险。
# 注意只放**文件读写层面**的词：编号、乱码、错字、缺页、扫描模糊……
# 「三方模板残留」「币种缺失」「无送达条款」虽然也是形式问题，但影响权利义务，必须留在风险清单里。
_TEXT_QUALITY_PATTERNS = (
    r"OCR",
    r"ocr",
    r"乱码",
    r"錯字",
    r"错字",
    r"別字",
    r"别字",
    r"识别错误",
    r"識別錯誤",
    r"文字错误",
    r"文字錯誤",
    r"排版",
    r"條款編號",
    r"条款编号",
    r"編號混亂",
    r"编号混乱",
    r"编号错误",
    r"編號錯誤",
    r"编号不连续",
    r"缺页",
    r"缺頁",
    r"页面缺失",
    r"頁面缺失",
    r"扫描不清",
    r"掃描不清",
    r"字迹模糊",
    r"字跡模糊",
    r"图片不清",
    r"圖片不清",
    r"转写错误",
    r"轉寫錯誤",
    r"格式错乱",
    r"格式錯亂",
    r"文档损坏",
    r"文檔損壞",
)

_TEXT_QUALITY_RE = re.compile("|".join(_TEXT_QUALITY_PATTERNS), re.IGNORECASE)


def is_text_quality(finding: Any) -> bool:
    """判断一条结论是不是「原件质量问题」。

    只看标题和 issue：quote（原文引用）里出现"乱码"两字很正常——
    比如引用一段真的有乱码的原文，那是证据，不是结论。
    """
    if isinstance(finding, dict):
        title = str(finding.get("title") or "")
        issue = str(finding.get("issue") or "")
    else:
        title = str(getattr(finding, "title", "") or "")
        issue = str(getattr(finding, "issue", "") or "")
    blob = f"{title} {issue}"
    return bool(_TEXT_QUALITY_RE.search(blob))


def split_quality(findings: list[Any]) -> tuple[list[Any], list[Any]]:
    """把结论分成 (合同风险, 原件质量问题)，各自保持原顺序。"""
    risks: list[Any] = []
    quality: list[Any] = []
    for finding in findings:
        (quality if is_text_quality(finding) else risks).append(finding)
    return risks, quality


__all__ = ["is_text_quality", "split_quality"]
