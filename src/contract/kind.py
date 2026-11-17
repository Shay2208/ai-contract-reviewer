"""合同类型识别与时效检查（纯规则，不依赖模型）。

为什么需要：
- 「补充协议 / 续签协议 / 变更协议」通常只有两三页，专门约定"改了什么"，
  条款缺失是**正常且预期的**——未约定事项一律依主协议执行。若按主合同口径体检，
  会把主协议本该管的条款全报成"缺失"，制造十几条假高风险。
- 合同/服务期已届满这类硬事实，必须由规则层判定，不能指望模型"记得看日期"。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

# 明显是「从属/增量」类合同的词
SUPPLEMENT_WORDS = (
    "补充协议",
    "補充協議",
    "补充合同",
    "補充合同",
    "补充条款",
    "变更协议",
    "變更協議",
    "续签协议",
    "續簽協議",
    "续费补充",
    "延长期限",
    "supplement",
    "amendment",
)

# 正文里出现这些表述，说明它明确指向另一份主合同
SUPPLEMENT_HINTS = (
    "主协议",
    "主合同",
    "原协议",
    "原合同",
    "本协议作为",
    "遵照主协议",
    "依主协议",
    "本补充协议",
)

_DATE_IN_BRACKETS = re.compile(
    r"[\[［【]?\s*(\d{4})\s*[\]］】]?\s*[年.\-/]\s*[\[［【]?\s*(\d{1,2})\s*[\]］】]?\s*[月.\-/]\s*[\[［【]?\s*(\d{1,2})\s*[\]］】]?\s*日?"
)
_TO_PATTERN = re.compile(r"(?:至|到|截至|有效期至|期限至)[^。；\n]{0,20}" + _DATE_IN_BRACKETS.pattern)


def detect_supplement(path: str | Path | None, text: str) -> tuple[bool, str]:
    """判断是不是补充/变更/续签类协议，返回（是否, 依据）。"""
    name = Path(path).name if path else ""
    lower_name = name.lower()
    for word in SUPPLEMENT_WORDS:
        hit = word in lower_name if word.isascii() else word in name
        if hit:
            return True, f"文件名含「{word}」"
    head = text[:600]
    for word in SUPPLEMENT_WORDS:
        if word.isascii():
            if word in text.lower():
                return True, f"正文含「{word}」"
        elif word in head:
            return True, f"正文含「{word}」"
    hits = [h for h in SUPPLEMENT_HINTS if h in text]
    if len(hits) >= 2:
        return True, f"正文多处指向主合同（{'、'.join(hits[:3])}）"
    return False, ""


def find_period_end(text: str) -> tuple[str, date | None, str]:
    """找合同/服务期限的结束日期。

    返回（原文片段, 结束日期, 说明）。找不到日期时日期为 None。
    """
    candidates: list[tuple[date, str]] = []
    for m in _TO_PATTERN.finditer(text):
        y, mo, d = (int(m.group(i)) for i in (1, 2, 3))
        try:
            value = date(y, mo, d)
        except ValueError:
            continue
        candidates.append((value, m.group(0).strip()))
    if not candidates:
        return "", None, "未在正文中识别到明确的合同期限结束日"
    candidates.sort(key=lambda x: x[0])
    latest, quote = candidates[-1]
    return quote, latest, ""


def is_expired(end: date | None, today: date | None = None) -> bool:
    if end is None:
        return False
    return end < (today or date.today())


# 预付全款但没有任何退款/终止结算安排
_PREPAY = re.compile(r"(?:预付|提前[^。；\n]{0,20}支付|先行支付|款到[^。；\n]{0,10}(?:后|再))")
_REFUND = re.compile(r"(退款|退还|返还|终止后[^。；\n]{0,20}结算|解除后[^。；\n]{0,20}结算)")
_ANNUAL_FULL = re.compile(r"(按年|年费|年度服务费|全额|一次性支付|100%)")


def find_prepay_without_refund(text: str) -> tuple[bool, str, bool]:
    """是否「预付且无退款/终止结算」。返回（命中, 原文, 是否按年全额）。"""
    m = _PREPAY.search(text)
    if not m:
        return False, "", False
    if _REFUND.search(text):
        return False, "", False
    sent = ""
    for chunk in re.split(r"[。；\n]", text):
        if _PREPAY.search(chunk):
            sent = chunk.strip()[:120]
            break
    annual = bool(_ANNUAL_FULL.search(text))
    return True, sent or m.group(0), annual
