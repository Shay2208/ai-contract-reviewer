"""合同梗概：从原文抽出类型、我方/对方主体、标的、金额、付款、交付、期限。

只用规则模式时，这一块就是报告「合同梗概」的全部内容；模型模式会用它做兜底，
模型给出的非空字段再覆盖上去。
"""

from __future__ import annotations

import re
from pathlib import Path

from .evidence import _first_sentence_with
from .rules.amount import _find_amount
from .rules.party import find_counterparty_name

_LABEL_PREFIX = re.compile(
    r"^(?:合同标的|标的名称|标的|合同内容|货物名称|服务内容|产品名称|项目名称|"
    r"付款方式|付款条件|付款安排|结算方式|交货时间|交付时间|交货地点|"
    r"合同金额|合同总价|合同价款|合同期限|服务期限|有效期)[：:]\s*"
)


def _strip_label(text: str) -> str:
    """去掉「合同标的：」「付款方式：」这类字段前缀，只留内容。"""
    return _LABEL_PREFIX.sub("", text).strip()


_BRACKETS = re.compile(r"[\[\]［］【】｛｝]")
_NEWLINES = re.compile(r"[\r\n]+")


def _flatten(text: str) -> str:
    r"""把 PDF/OCR 的软换行和填空方括号去掉，便于正则提取梗概。

    合同模板里常见「人民币［10000］元/年」「期限为［1］年」这类填空写法，
    方括号会把 \d 量词匹配打断，导致金额、期限全部抽不出来。
    """
    return _NEWLINES.sub("", _BRACKETS.sub("", text))


def _guess_contract_type(path: Path, text: str) -> str:
    """按标题区关键词猜合同类型（采购/销售/服务…），都猜不出就写「合同」。"""
    mapping = [
        ("采购", ("采购", "购买", "供货", "购销")),
        ("销售", ("销售", "供货", "购销")),
        ("服务", ("服务", "咨询", "运维", "推广", "代运营")),
        ("运输/物流", ("运输", "物流", "货运", "承运")),
        ("租赁", ("租赁", "出租", "承租")),
        ("框架协议", ("框架", "年度协议")),
    ]
    head = text[:400]
    best, score = "", 0
    for name, words in mapping:
        hit = sum(head.count(w) for w in words)
        if hit > score:
            best, score = name, hit
    return best or "合同"


def _rule_summary(path: Path, text: str, settings) -> dict:
    """规则模式下的合同梗概：类型、双方主体、标的、金额、付款、交付、期限。"""
    flat = _flatten(text)
    our_party = ""
    our_side = ""
    for name in settings.our_companies:
        if name and name in text:
            pos = text.find(name)
            window = text[max(0, pos - 80): pos + 80]
            if "甲方" in window:
                our_side = "甲方"
            elif "乙方" in window:
                our_side = "乙方"
            our_party = f"{name}（{our_side or '身份待确认'}）"
            break
    if our_side:
        counterparty = find_counterparty_name(flat, "乙方" if our_side == "甲方" else "甲方")
    else:
        # 没配置 OUR_COMPANIES 时无从判断立场，就把两边都列出来，别猜
        sides = [find_counterparty_name(flat, side) for side in ("甲方", "乙方")]
        counterparty = " ／ ".join(s for s in sides if s)
    amount_text, amount_value = _find_amount(flat)
    return {
        "contract_type": _guess_contract_type(path, text),
        "our_party": our_party or "（未在文本中识别到我方主体，请确认）",
        "counterparty": counterparty,
        "subject": _strip_label(_first_sentence_with(flat, ("采购", "销售", "提供", "委托", "标的", "服务内容", "产品名称"), 100)),
        "amount": f"{amount_value:,.2f} 元" if amount_value else (amount_text or "（未找到明确金额）"),
        "payment_terms": _strip_label(_first_sentence_with(flat, ("预付", "付款", "结算", "账期"), 100)),
        "delivery_terms": _strip_label(_first_sentence_with(flat, ("交货", "交付", "到货", "发货"), 100)),
        "term": _strip_label(_first_sentence_with(flat, ("有效期", "合同期限", "服务期限"), 100)),
    }


__all__ = ["_rule_summary", "_flatten", "_strip_label", "_guess_contract_type"]
