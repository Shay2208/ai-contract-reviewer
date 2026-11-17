"""争议解决：仲裁与诉讼同时约定（规则 2）、管辖地不在我方所在地（规则 5）。

两条都只看争议解决那一段，不改写原文，只提示「约定不明」和「异地成本」。
"""

from __future__ import annotations

import re

from ..evidence import _first_sentence_with
from ..models import HIGH, MEDIUM, Finding


def rule_arbitration_and_litigation(text: str) -> list[Finding]:
    """规则 2：既约定仲裁又约定法院诉讼，属于约定不明。"""
    findings: list[Finding] = []
    has_arb = "仲裁" in text
    has_court = "人民法院" in text or "诉讼" in text
    if has_arb and has_court:
        m = re.search(r"[^。；\n]{0,40}(?:仲裁|人民法院|诉讼)[^。；\n]{0,60}", text)
        findings.append(
            Finding(
                level=HIGH,
                title="仲裁与诉讼同时约定",
                clause="争议解决",
                quote=(m.group(0).strip() if m else ""),
                issue="既约定仲裁又约定法院诉讼，属于约定不明，对方可能据此主张管辖条款无效、拖延争议解决。",
                suggestion="二选一：要么保留「提交 XX 仲裁委员会仲裁」，要么改为「向 XX 人民法院起诉」，删除另一句。",
            )
        )
    return findings


def rule_jurisdiction(text: str, settings, our_side: str = "") -> list[Finding]:
    """规则 5：争议解决地不在我方所在地（异地诉讼/仲裁成本更高）。

    已写「我方所在地」或我方一侧所在地（如我方是甲方、合同写甲方所在地）都算有利约定。
    """
    findings: list[Finding] = []
    has_arb = "仲裁" in text
    has_court = "人民法院" in text or "诉讼" in text
    if settings.our_locations and (has_arb or has_court):
        dispute_sent = _first_sentence_with(text, ("仲裁", "人民法院", "管辖"), 200)
        favorable = any(loc in dispute_sent for loc in settings.our_locations) or "我方所在地" in dispute_sent
        if our_side and f"{our_side}所在地" in dispute_sent:
            favorable = True
        if not favorable:
            findings.append(
                Finding(
                    level=MEDIUM,
                    title="争议解决地不在我方所在地",
                    clause="争议解决",
                    quote=dispute_sent,
                    issue=f"约定地点未体现我方所在地（{'/'.join(settings.our_locations)}），异地诉讼/仲裁的时间与费用成本更高。",
                    suggestion="优先改为我方所在地法院/仲裁机构；对方不同意时，至少约定「由被告所在地法院管辖」。",
                )
            )
    return findings


__all__ = ["rule_arbitration_and_litigation", "rule_jurisdiction"]
