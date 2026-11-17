"""期限与履行：合同期已届满（规则 10）、预付但无退款安排（规则 11）、明确排除服务（规则 12）。

这三条都依赖日期/付款/服务范围的硬事实，交给 kind 模块做识别，规则层只负责定级和写文案。
"""

from __future__ import annotations

import re

from .. import kind
from ..models import HIGH, MEDIUM, Finding


def rule_expired_period(text: str) -> list[Finding]:
    """规则 10：合同/服务期已届满（硬事实，必须规则判定）。"""
    findings: list[Finding] = []
    end_quote, end_date, _why = kind.find_period_end(text)
    if kind.is_expired(end_date):
        findings.append(
            Finding(
                level=HIGH,
                title="合同期限已届满",
                clause="合同期限",
                quote=end_quote,
                issue=f"约定的期限已于 {end_date.strftime('%Y-%m-%d')} 到期，若仍在履行或需要继续使用，目前没有有效的合同依据。",
                suggestion="确认是否已续签/实际仍在履行；如需继续，尽快补签新的协议或书面确认延长，并写清续期费用与生效日。",
            )
        )
    return findings


def rule_prepay_without_refund(text: str) -> list[Finding]:
    """规则 11：要求先付款，但全文没有退款/终止结算安排。"""
    findings: list[Finding] = []
    prepay_hit, prepay_quote, annual = kind.find_prepay_without_refund(text)
    if prepay_hit:
        findings.append(
            Finding(
                level=HIGH if annual else MEDIUM,
                title="预付后缺少退款安排",
                clause="价款与支付",
                quote=prepay_quote,
                issue="款项需先付，但全文未约定对方不能履约、服务中止或合同解除时的退款与结算方式，付款后我方缺少救济手段。",
                suggestion="补充「若乙方未按约提供服务，甲方有权按未履行期间比例要求退款」及解除后的结算与退出安排。",
            )
        )
    return findings


def rule_excluded_service(text: str) -> list[Finding]:
    """规则 12：明确排除某类服务（续费/补充协议里最常见的实质性减配）。"""
    findings: list[Finding] = []
    excluded = re.search(r"[^。；\n]{0,10}不(?:再)?提供[^。；\n]{0,24}(?:服务|开发|实施|培训|维护|支持)[^。；\n]{0,20}", text)
    if excluded:
        findings.append(
            Finding(
                level=MEDIUM,
                title="对方明确不提供部分服务",
                clause="服务范围",
                quote=excluded.group(0).strip()[:120],
                issue="本条把某类服务排除在本次合作范围外（或改为另行收费），若我方实际需要，后续只能另行谈判付费。",
                suggestion="确认我方是否真的不需要该服务；如需保留，在协议中写明服务范围与费用，避免后续被动加价。",
            )
        )
    return findings


__all__ = ["rule_expired_period", "rule_prepay_without_refund", "rule_excluded_service"]
