"""清单缺失项：把检查表里「没写」的条目转成风险结论（规则 1）。

这条规则不是正则匹配，而是「清单体检结果 → 风险清单」的翻译，但要过两道闸：
正文其实写了的不报，且最多留 2 条。
"""

from __future__ import annotations

from ..checklist import CheckResult, missing_items
from ..evidence import _contradicted_by_text
from ..models import LEVEL_ORDER, LOW, Finding
from .amount import _find_amount

# 「缺少某条款」类结论最多进风险清单几条（其余只留在检查表里）
_MISSING_FINDING_CAP = 2


def rule_missing_clauses(
    text: str,
    checks: list[CheckResult],
    settings,
    supplement: bool = False,
) -> list[Finding]:
    """规则 1：清单里 high/medium 的缺失项进风险清单，low 只落检查表。

    补充/变更/续签协议：未约定事项依主协议执行，缺失是正常的，不进风险清单。

    两条硬约束（2026-11-16 行政反馈后加）：
    a. 必须过"正文是否其实写了"的检验——规则层生成的缺失项原先不过这道闸，
       结果合同里明明写了「逾期未提异议视为弃权」，仍报「缺少逾期未提异议视为合格」。
    b. 最多留 2 条。行政的原话是"缺少保密条款？一万块的监控安装，保密什么"——
       十几条「缺少 XX」会把真正值钱的风险挤出视野，也让评论没法看。
    """
    findings: list[Finding] = []
    if supplement:
        return findings
    pairs = [
        (result.item, Finding(
            level=result.item.risk,
            title=f"缺少「{result.item.title}」",
            issue="合同中未检索到相关约定。",
            suggestion=result.item.suggestion,
            source="rule",
            missing=True,
        ))
        for result in missing_items(checks)
        if result.item.risk != LOW
    ]
    pairs = [(item, f) for item, f in pairs if not _contradicted_by_text(f, text)]
    # 小额合同（10 万以下）不拿"缺保密/缺知识产权"烦人：
    # 行政原话"一万块的监控安装，保密什么"——只保留必须项。
    _, amount_value = _find_amount(text)
    if 0 < amount_value < 100_000:
        pairs = [(item, f) for item, f in pairs if item.must_ask]
    pairs.sort(key=lambda pair: LEVEL_ORDER.get(pair[1].level, 3))
    findings.extend(f for _, f in pairs[:_MISSING_FINDING_CAP])
    return findings


__all__ = ["_MISSING_FINDING_CAP", "rule_missing_clauses"]
