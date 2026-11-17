"""规则引擎入口：把 14 条确定性规则汇成一个 run_rules()。

每条规则是一个独立函数、一段中文 docstring、返回 list[Finding]；
编排层只负责调用与合并，不再关心单条规则的实现细节。
"""

from __future__ import annotations

from ..checklist import CheckResult
from ..evidence import _detect_our_side
from ..models import Finding
from .amount import _amount_variants, _find_amount, rule_amount_mismatch
from .jurisdiction import rule_arbitration_and_litigation, rule_jurisdiction
from .missing import _MISSING_FINDING_CAP, rule_missing_clauses
from .party import find_counterparty_name, rule_hollow_subject, rule_personal_account
from .penalty import _penalty_payee, rule_daily_penalty, rule_penalty_ratio
from .period import rule_excluded_service, rule_expired_period, rule_prepay_without_refund
from .power import rule_open_loss, rule_unilateral_standard, rule_unilateral_termination

__all__ = [
    "run_rules",
    "rule_missing_clauses",
    "rule_arbitration_and_litigation",
    "rule_daily_penalty",
    "rule_penalty_ratio",
    "rule_personal_account",
    "rule_jurisdiction",
    "rule_unilateral_standard",
    "rule_unilateral_termination",
    "rule_open_loss",
    "rule_expired_period",
    "rule_prepay_without_refund",
    "rule_excluded_service",
    "rule_hollow_subject",
    "rule_amount_mismatch",
]


def run_rules(
    text: str,
    checks: list[CheckResult],
    settings,
    supplement: bool = False,
) -> list[Finding]:
    """按固定顺序跑完全部规则，返回未合并的原始结论（顺序即风险呈现顺序）。

    supplement=True 时跳过「清单缺失项」（补充协议未约定事项依主协议执行）。
    """
    findings: list[Finding] = []
    our_side = _detect_our_side(text, settings)

    findings.extend(rule_missing_clauses(text, checks, settings, supplement=supplement))
    findings.extend(rule_arbitration_and_litigation(text))
    findings.extend(rule_daily_penalty(text))
    findings.extend(rule_penalty_ratio(text, settings, our_side))
    findings.extend(rule_personal_account(text))
    findings.extend(rule_jurisdiction(text, settings, our_side))
    findings.extend(rule_unilateral_standard(text))
    findings.extend(rule_unilateral_termination(text))
    findings.extend(rule_open_loss(text))
    findings.extend(rule_expired_period(text))
    findings.extend(rule_prepay_without_refund(text))
    findings.extend(rule_excluded_service(text))
    findings.extend(rule_hollow_subject(text))
    findings.extend(rule_amount_mismatch(text))
    return findings
