"""单方权利与敞口：标准由单方决定（规则 6）、单方解除/变更权（规则 8）、赔偿范围敞口（规则 9）。

三条都是「条款写得太宽」类风险：不违法，但把裁量权和不确定性留给了对方。
"""

from __future__ import annotations

import re

from ..models import MEDIUM, Finding


def rule_unilateral_standard(text: str) -> list[Finding]:
    """规则 6：验收/质量标准写成「以某方要求为准」，缺乏客观标准。"""
    findings: list[Finding] = []
    vague = re.search(r"以(?:甲方|乙方)(?:的)?要求为准", text)
    if vague:
        findings.append(
            Finding(
                level=MEDIUM,
                title="标准由单方决定",
                clause="验收/质量",
                quote=vague.group(0),
                issue="「以某方要求为准」缺乏客观标准，验收时容易扯皮，也不利于举证。",
                suggestion="改为可验证的标准：国标/行标编号、双方确认的样品或技术协议，并约定验收方法与期限。",
            )
        )
    return findings


def rule_unilateral_termination(text: str) -> list[Finding]:
    """规则 8：一方享有单方解除/终止/变更权，权利义务不对等。"""
    findings: list[Finding] = []
    unilateral = re.search(r"(?:甲方|乙方)有权单方(?:解除|终止|变更)[^。；\n]{0,30}", text)
    if unilateral:
        findings.append(
            Finding(
                level=MEDIUM,
                title="存在单方解除/变更权",
                clause="解除与退出",
                quote=unilateral.group(0).strip(),
                issue="单方解除权若只给一方，权利义务不对等；需确认是否给了我方对等权利。",
                suggestion="改为双方对等，或为我方争取同样的单方解除权与补偿安排。",
            )
        )
    return findings


def rule_open_loss(text: str) -> list[Finding]:
    """规则 9：赔偿范围写成「一切损失/全部损失/无限责任」，没有上限。"""
    findings: list[Finding] = []
    open_loss = re.search(r"[^。；\n]{0,30}(?:一切损失|全部损失|所有损失|无限责任)[^。；\n]{0,30}", text)
    if open_loss:
        findings.append(
            Finding(
                level=MEDIUM,
                title="赔偿范围敞口",
                clause="违约责任",
                quote=open_loss.group(0).strip(),
                issue="「一切损失」等表述没有上限，可能被主张包括间接损失与可得利益。",
                suggestion="限定为「直接损失」，并约定赔偿总额不超过合同金额（或已付款项）的 X%。",
            )
        )
    return findings


__all__ = ["rule_unilateral_standard", "rule_unilateral_termination", "rule_open_loss"]
