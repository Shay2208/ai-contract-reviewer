"""金额识别与一致性：从原文认出「合同总价」，并发现前后口径矛盾（规则 14）。

金额是行政最常自己核、也最该在盖章前问清楚的一项：正文一个数、附件另一个数，
盖章后对账、开票、报销第一个对不上。
"""

from __future__ import annotations

import re

from ..evidence import _sentences
from ..models import HIGH, Finding

_NUM = r"(\d[\d,，]*(?:\.\d+)?)"
_AMOUNT_SENT_KEYS = ("合同总价", "合同金额", "总金额", "总价款", "价款合计", "合同价款")
# 「200万网络红外高清摄像机」「400万像素」这类是设备规格，不是金额
_SPEC_NOT_MONEY = re.compile(r"\s*(?:像素|网络|红外|高清|摄像机|监控头|分辨率|镜头|硬盘|接口|mm)")


def _amount_value(num_text: str, unit: str) -> float | None:
    """把「1,200,000」「120 万」换算成元；解析失败返回 None。"""
    try:
        value = float(num_text.replace(",", "").replace("，", ""))
    except ValueError:
        return None
    return value * 10000 if unit in ("万元", "万") else value


def _find_amount(text: str) -> tuple[str, float]:
    """返回（原文金额片段, 换算成元的数值）。优先取「合同总金额」所在句子。"""
    # 0) 先认带标签的总价/总造价（最准）
    #    实测坑：安防合同写「200 万网络红外高清摄像机」，把摄像机像素当成了 200 万元合同额。
    variants = _amount_variants(text)
    if variants:
        return variants[0]

    # 1) 明确的合同总价句子（避免把单价/违约金基数当成合同额）
    for sent in _sentences(text):
        if not any(k in sent for k in _AMOUNT_SENT_KEYS):
            continue
        for pattern in (r"[¥￥]\s*" + _NUM, _NUM + r"\s*(万元|万|元)"):
            m = re.search(pattern, sent)
            if m:
                unit = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
                value = _amount_value(m.group(1), unit or "")
                if value:
                    return m.group(0), value
    # 2) 全文取最大值兜底
    best_text, best_value = "", 0.0
    for m in re.finditer(_NUM + r"\s*(万元|万|元)", text):
        if _SPEC_NOT_MONEY.search(m.group(0) + text[m.end(): m.end() + 4]):
            continue  # 「200万网络红外摄像机」这类是设备规格，不是钱
        value = _amount_value(m.group(1), m.group(2)) or 0.0
        if value > best_value:
            best_text, best_value = m.group(0), value
    for m in re.finditer(r"[¥￥]\s*" + _NUM, text):
        value = _amount_value(m.group(1), "") or 0.0
        if value > best_value:
            best_text, best_value = m.group(0), value
    return best_text, best_value


_AMOUNT_LABELS = (
    r"工程总造价|合同总造价|总造价|合同总价|合同总金额|合同价款|总金额|价款合计|工程总价"
    r"|开[专專普]?票价格|含税价格|含税价|合计金额"
)
_AMOUNT_RE = re.compile(
    r"(?:" + _AMOUNT_LABELS + r")\s*(?:为)?\s*[：:]?\s*[¥￥]?\s*(\d[\d,，]*(?:\.\d+)?)\s*(万元|万)?"
)


def _amount_variants(text: str) -> list[tuple[str, float]]:
    """抓出所有「总价/总金额/开票价/含税价」口径的金额，用于判断是否自相矛盾。"""
    out: list[tuple[str, float]] = []
    seen: set[float] = set()
    for m in _AMOUNT_RE.finditer(text):
        try:
            value = float(m.group(1).replace(",", "").replace("，", ""))
        except ValueError:
            continue
        if m.group(2):
            value *= 10000
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        out.append((m.group(0).strip(), value))
    return out


def rule_amount_mismatch(text: str) -> list[Finding]:
    """规则 14：金额口径前后不一致（正文总价 vs 附件合计 / 开票价）。

    行政视角：这是她唯一能立刻自己核、也最该在盖章前问清楚的事——
    盖章盖的是 10057，附件算出来 8905，将来对账开票报销第一个找行政。
    """
    findings: list[Finding] = []
    variants = _amount_variants(text)
    if len(variants) >= 2:
        diff = max(v for _, v in variants) - min(v for _, v in variants)
        if diff > 1:
            top = sorted(variants, key=lambda x: -x[1])[:3]
            findings.append(
                Finding(
                    level=HIGH,
                    title="合同金额前后不一致",
                    clause="价款与支付",
                    quote="；".join(snippet for snippet, _ in top)[:180],
                    issue=(
                        f"正文与附件/开票口径出现 {len(variants)} 个不同金额"
                        f"（{'、'.join(f'{v:,.2f}' for _, v in top)}），最大相差 {diff:,.2f} 元，"
                        "盖章后对账、开票、报销都会对不上。"
                    ),
                    suggestion=(
                        "用印前让业务书面确认：按哪个金额签、税率多少、开专票还是普票，"
                        "并让正文与附件的数字保持一致。"
                    ),
                )
            )
    return findings


__all__ = ["_find_amount", "_amount_variants", "_amount_value", "rule_amount_mismatch"]
