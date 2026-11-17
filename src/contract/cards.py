"""合同预审相关的飞书卡片 JSON。

不依赖飞书 SDK：生成标准卡片 JSON，既能被自建应用发送，
也能直接打进群自定义机器人的 webhook。
"""

from __future__ import annotations

from typing import Any

RISK_TEMPLATE = {"高": "red", "中高": "orange", "中": "orange", "低": "green", "—": "grey"}
LEVEL_ICON = {"high": "🔴", "medium": "🟡", "low": "🟢"}
LEVEL_LABEL = {"high": "高", "medium": "中", "low": "低"}


def _md(content: str) -> dict[str, Any]:
    return {"tag": "lark_md", "content": content}


def _div(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": _md(content)}


def _hr() -> dict[str, Any]:
    return {"tag": "hr"}


def _note(content: str) -> dict[str, Any]:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


def _money(value: str) -> str:
    return (value or "—").strip() or "—"


def review_card(record: dict[str, Any]) -> dict[str, Any]:
    """一份合同的预审结果卡片：结论 + 金额/对方 + Top 风险 + 三个按钮。"""
    summary = record.get("summary") or {}
    findings = record.get("findings") or []
    overall = record.get("overall") or "—"
    file_name = record.get("file_name") or "合同"

    high = [f for f in findings if f.get("level") == "high"]
    top = high[:3] or [f for f in findings if f.get("level") == "medium"][:3]

    lines = [
        f"**综合风险**：{overall}　（高 {record.get('high', 0)} ｜ 中 {record.get('medium', 0)} ｜ 低 {record.get('low', 0)}）",
        f"**合同类型**：{summary.get('contract_type') or '—'}　**合同金额**：{_money(summary.get('amount'))}",
        f"**我方**：{summary.get('our_party') or '—'}",
        f"**对方**：{summary.get('counterparty') or '—'}",
    ]

    elements: list[dict[str, Any]] = [
        _div("\n".join(lines)),
        _hr(),
    ]

    if top:
        risk_lines = []
        for i, item in enumerate(top, 1):
            icon = LEVEL_ICON.get(item.get("level", ""), "•")
            risk_lines.append(f"{i}. {icon} **{item.get('title', '')}**")
            if item.get("quote"):
                risk_lines.append(f"　 > 原文：{item['quote']}")
        elements.append(_div("\n".join(risk_lines)))
        if len(high) > 3:
            elements.append(_note(f"另有 {len(high) - 3} 条高风险，查看完整报告"))
    else:
        elements.append(_div("未发现明显高风险条款。"))

    elements.append(_hr())
    elements.append(_note(f"报告已生成：{record.get('report_docx') or record.get('report_md') or '本地 out/contracts'}"))

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": RISK_TEMPLATE.get(overall, "grey"),
            "title": {"tag": "plain_text", "content": f"合同预审 · {file_name}"},
        },
        "elements": elements,
    }


def comment_preview_card(record: dict[str, Any], comment_text: str) -> dict[str, Any]:
    """私聊预览卡片：把「审批单上会出现的评论」原文先摆出来，再附详情。

    同事拿它试模板：看到的评论一字不差就是真实审批单里那段。
    """
    overall = record.get("overall") or "—"
    file_name = record.get("file_name") or "合同"
    findings = record.get("findings") or []
    high = [f for f in findings if f.get("level") == "high"][:5]

    elements: list[dict[str, Any]] = [
        _div(
            "**审批单上会出现的评论（原文，一字不差）**\n"
            "同事在飞书审批里看到的就是下面这段，请照此评审模板："
        ),
        _hr(),
        _div(comment_text),
        _hr(),
    ]

    if high:
        risk_lines = []
        for i, item in enumerate(high, 1):
            risk_lines.append(f"{i}. {LEVEL_ICON.get('high', '🔴')} **{item.get('title', '')}**")
            if item.get("quote"):
                risk_lines.append(f"　 > 原文：{item['quote']}")
        elements.append(_div("**高风险明细（预览不截断）**\n" + "\n".join(risk_lines)))
    else:
        elements.append(_div("未发现高风险条款。"))

    elements.append(_hr())
    elements.append(_note("表单核对行只在审批表单有数据时出现（私聊预览无表单，故省略）。模板意见直接回在这里即可：哪条该留、哪条该删、措辞怎么改。"))

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": RISK_TEMPLATE.get(overall, "grey"),
            "title": {"tag": "plain_text", "content": f"审批评论预览 · {file_name}"},
        },
        "elements": elements,
    }


def help_card() -> dict[str, Any]:
    """用户不知道怎么用时回这个。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "合同 AI 预审 · 使用说明"}},
        "elements": [
            _div(
                "**直接把合同文件发给我**（支持 Word `.docx`、文字版 PDF、`.txt`），"
                "我会在 30 秒内回一张卡片：\n"
                "1. **审批单上会出现的那段评论原文**（一字不差，用于评审模板）\n"
                "2. 高风险明细（附原文，预览不截断）"
            ),
            _hr(),
            _div(
                "**能查什么**：主体信息、标的与验收、金额与含税、付款节点与账户、"
                "违约责任与上限、解除与结算、争议解决与管辖、担保措施等 31 项。"
            ),
            _note("预览不写审批、不发通知，可反复发给机器人改模板。"),
            _note("AI 体检不替代法务意见；金额、比例、账户请对照原文复核。"),
        ],
    }


__all__ = [
    "comment_preview_card",
    "help_card",
    "review_card",
]
