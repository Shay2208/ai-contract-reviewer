"""违约金方向与比例：按日违约金是否过高（规则 3a）、违约金总额比例是否过高（规则 3b）。

关键在「谁收钱」：合同里「丙方违约→向甲方支付 10%」这种是赔给我方的钱，
比例越高越有利，不能报"比例偏高"（报成偏高等于建议把这笔钱砍掉）。
"""

from __future__ import annotations

import re

from ..models import HIGH, MEDIUM, Finding


def _penalty_payee(ctx: str) -> str:
    """从违约金上下文判断「谁收钱」：「向甲方支付」→ 甲方是收款方。"""
    m = re.search(r"向\s*(甲方|乙方|丙方)[^。；]{0,10}(?:支付|交纳|缴纳|承担|赔偿)", ctx)
    return m.group(1) if m else ""


def rule_daily_penalty(text: str) -> list[Finding]:
    """规则 3a：按日计算的逾期违约金过高（≥0.5%/日，年化远超 LPR 四倍）。"""
    findings: list[Finding] = []
    daily = re.search(r"(?:每逾期一日|每逾期1日|每日)[^。；\n]{0,25}?(\d+(?:\.\d+)?)\s*%", text)
    daily_pct = 0.0
    if daily:
        daily_pct = float(daily.group(1))
    else:
        m2 = re.search(r"日万分之([\d.]+)", text)
        if m2:
            daily_pct = float(m2.group(1)) / 100
    if daily_pct >= 0.5:
        quote = daily.group(0).strip() if daily else f"日万分之{m2.group(1)}"
        findings.append(
            Finding(
                level=HIGH,
                title="逾期违约金按日计算过高",
                clause="违约责任",
                quote=quote,
                issue=f"按日 {daily_pct:g}% 计算，年化远超 LPR 四倍，容易被认定过高而调减，且对方可能反过来主张。",
                suggestion="改为按日万分之五左右（年化约 18%），或按 LPR 的 1.5–2 倍计算，并设累计上限。",
            )
        )
    return findings


def rule_penalty_ratio(text: str, settings, our_side: str = "") -> list[Finding]:
    """规则 3b：违约金总额比例超过常见上限。

    先判方向：赔给我方的比例不算风险；只有我方（或方向不明）要付的钱才算偏高。
    """
    findings: list[Finding] = []
    total_hits: list[float] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", text):
        ctx = text[max(0, m.start() - 50): m.end() + 50]
        if "违约金" not in ctx or re.search(r"每日|每逾期", ctx):
            continue
        payee = _penalty_payee(ctx)
        if our_side and payee == our_side:
            continue  # 对方赔给我方，对我方有利，不算过高
        total_hits.append(float(m.group(1)))
    for pct in total_hits:
        if pct >= settings.contract_penalty_max_pct:
            m = re.search(r"[^。；\n]{0,20}" + re.escape(f"{pct:g}%") + r"[^。；\n]{0,30}", text)
            findings.append(
                Finding(
                    level=HIGH if pct >= 50 else MEDIUM,
                    title=f"违约金比例偏高（{pct:g}%）",
                    clause="违约责任",
                    quote=(m.group(0).strip() if m else f"{pct:g}%"),
                    issue=f"约定比例 {pct:g}%，超过常见上限（{settings.contract_penalty_max_pct:g}%），过高可能被法院调减，也可能被对方以显失公平抗辩。",
                    suggestion=f"建议将违约金总额控制在合同金额的 {settings.contract_penalty_max_pct:g}% 以内，并写明计算基数。",
                )
            )
            break
    return findings


__all__ = ["_penalty_payee", "rule_daily_penalty", "rule_penalty_ratio"]
