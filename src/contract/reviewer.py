"""合同预审编排：规则清单 + 语义审查 + 引用回验 + 风险合并。

分工：
- models.py   数据模型（Finding / ReviewResult / 定级）
- evidence.py 通用工具（归一化、引用回验、句子切分、结论校验）
- rules/      14 条确定性规则，每条一个函数
- summary.py  合同梗概抽取
- 本文件      主流程 review_contract()、合并去重 _merge()、追问 _build_questions()

为保持向后兼容，本文件继续导出历史公开名（Finding、ReviewResult、_merge、
_rule_findings、quote_in_text 等），外部 `from src.contract.reviewer import ...` 不受影响。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from ..llm import LLMClient
from . import kind
from .checklist import (  # noqa: F401  （LOW / LEVEL_LABEL 仅再导出）
    HIGH,
    LEVEL_LABEL,
    LOW,
    MEDIUM,
    CheckResult,
    missing_items,
    run_checks,
)
from .evidence import (  # noqa: F401  （再导出：历史公开名）
    _contradicted_by_text,
    _detect_our_side,
    _first_sentence_with,
    _harmful_penalty_advice,
    _is_noise,
    _normalize,
    quote_in_text,
)
from .llm_reviewer import LLMReview, llm_review
from .models import LEVEL_ORDER, Finding, ReviewResult  # noqa: F401  （再导出）
from .parser import extract_text
from .rules import (  # noqa: F401  （再导出：历史公开名）
    _MISSING_FINDING_CAP,
    _amount_variants,
    _find_amount,
    _penalty_payee,
    run_rules,
)
from .summary import _rule_summary

log = logging.getLogger(__name__)

__all__ = [
    # 主流程
    "review_contract",
    "_rule_findings",
    "_merge",
    "_build_questions",
    "_rule_summary",
    # 再导出的历史公开名
    "Finding",
    "ReviewResult",
    "LEVEL_ORDER",
    "LEVEL_LABEL",
    "_normalize",
    "quote_in_text",
    "_contradicted_by_text",
    "_is_noise",
    "_harmful_penalty_advice",
    "_first_sentence_with",
    "_find_amount",
    "_amount_variants",
    "_detect_our_side",
    "_penalty_payee",
    "_MISSING_FINDING_CAP",
]


def _rule_findings(text: str, checks: list[CheckResult], settings, supplement: bool = False) -> list[Finding]:
    """规则层入口：14 条确定性规则由 rules 包实现，这里只做转发。"""
    return run_rules(text, checks, settings, supplement=supplement)


def _merge(base: list[Finding], extra: list[Finding], text: str, our_side: str = "") -> list[Finding]:
    """合并规则与模型结果：回验引用、去重、否决"明明写了却报缺失"的结论。"""
    merged = list(base)
    seen = {(_normalize(f.title), _normalize(f.quote)[:20]) for f in base}
    quotes = [_normalize(f.quote) for f in base if _normalize(f.quote)]
    for f in extra:
        if f.quote and not quote_in_text(f.quote, text):
            continue  # 引用回验失败，丢弃
        if _contradicted_by_text(f, text):
            continue  # 正文里有相反证据，丢弃
        if _is_noise(f):
            continue  # 纯措辞/格式类，丢弃
        if _harmful_penalty_advice(f, our_side):
            continue  # 建议给我方加责任的结论，方向反了
        key = (_normalize(f.title), _normalize(f.quote)[:20])
        if key in seen:
            continue
        nq = _normalize(f.quote)
        if nq and any(len(nq) >= 12 and (nq in q or q in nq) for q in quotes if len(q) >= 12):
            continue  # 引的是同一句话，已有一条覆盖，避免重复报
        nt = _normalize(f.title)
        if nt and len(nt) >= 5 and any(
            nt in _normalize(g.title) or _normalize(g.title) in nt for g in merged
        ):
            continue  # 同一类问题已有一条（如规则「责任主体虚化」与模型「责任主体虚化风险」）
        seen.add(key)
        if nq:
            quotes.append(nq)
        merged.append(f)
    # 同级内先排有原文引用的具体条款，再排「合同未约定 XX」这类缺失项：
    # 行政看评论时最想先看到「哪一条写了什么坑」，缺失项本身信息量低。
    merged.sort(key=_sort_key)
    return merged


def _sort_key(f: Finding) -> tuple[int, int, float]:
    """排序键：先按风险等级，同级内有原文引用的具体条款排在「合同未约定 XX」之前。

    行政看评论时最想先看到「哪一条写了什么坑」，缺失项信息量低，压后面。
    """
    return (LEVEL_ORDER.get(f.level, 3), 0 if f.quote else 1, -f.confidence)


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    """规则模式不经过 _merge，这里单独保证顺序一致。"""
    return sorted(findings, key=_sort_key)


def _build_questions(
    checks: list[CheckResult],
    llm_questions: list[str],
    limit: int = 10,
    supplement: bool = False,
    text: str = "",
) -> list[str]:
    """生成「签约前必问对方的问题」：模型问题在前，清单兜底在后，去重后截断。"""
    questions: list[str] = []
    # 模型通读全文后提的问题更贴合这份合同，排在清单兜底问题前面
    for q in llm_questions:
        if q and q not in questions:
            questions.append(q)
    if supplement:
        # 补充协议不追问"缺少某条款"，改问主协议核对
        questions.append("请提供主协议，核对本协议未约定事项（保密、知识产权、违约责任等）在主协议中的约定。")
    else:
        # 小额合同（10 万以下）只问必须项，不拿"缺保密条款"烦人（与风险清单同口径）
        _, amount_value = _find_amount(text) if text else ("", 0.0)
        small = 0 < amount_value < 100_000

        def _really_missing(result: CheckResult) -> bool:
            """正文里其实写了的不算缺失（与风险清单同一道闸，避免「必问」里问已经有的东西）。"""
            if small and not result.item.must_ask:
                return False
            if not text:
                return True
            return not _contradicted_by_text(
                Finding(
                    level=result.item.risk,
                    title=f"缺少「{result.item.title}」",
                    issue="合同中未检索到相关约定。",
                ),
                text,
            )

        for result in missing_items(checks, HIGH):
            if not result.item.must_ask or not _really_missing(result):
                continue
            questions.append(f"合同未见「{result.item.title}」相关约定，能否补充：{result.item.suggestion}？")
        for result in missing_items(checks, MEDIUM):
            if len(questions) >= limit - 3:
                break
            if not _really_missing(result):
                continue
            questions.append(f"是否补充「{result.item.title}」：{result.item.suggestion}？")
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for q in questions:
        if q and q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[:limit]


def review_contract(path: str | Path, settings, force_rules: bool = False) -> ReviewResult:
    """审查一份合同：解析 → 清单体检 → 规则 →（可选）模型 → 合并 → 梗概与追问。"""
    p = Path(path)
    text = extract_text(p, settings)
    checks = run_checks(text)
    mode = "rules"

    is_supplement, evidence = kind.detect_supplement(p, text)

    llm = None if force_rules else LLMClient.from_settings(settings)
    llm_result = (
        LLMReview(error="已指定仅规则模式")
        if force_rules
        else llm_review(llm, text, settings, supplement=is_supplement)
    )

    findings = _rule_findings(text, checks, settings, supplement=is_supplement)

    if llm_result.ok:
        mode = "llm"
        llm_findings = [
            Finding(
                level=f["level"],
                title=f["title"],
                clause=f.get("clause", ""),
                quote=f.get("quote", ""),
                issue=f.get("issue", ""),
                suggestion=f.get("suggestion", ""),
                confidence=f.get("confidence", 0.7),
                source="llm",
                missing=bool(f.get("missing")),
            )
            for f in llm_result.findings
        ]
        findings = _merge(findings, llm_findings, text, our_side=_detect_our_side(text, settings))
        summary = dict(_rule_summary(p, text, settings))
        summary.update({k: v for k, v in llm_result.summary.items() if v})
    else:
        summary = _rule_summary(p, text, settings)

    notes: list[str] = []
    if is_supplement:
        notes.append(f"识别为补充/变更类协议（{evidence}）：未约定事项依主协议执行，缺失条款不计风险，请另行核对主协议。")
        # 模型仍可能按主合同口径报「缺少 XX 条款」，在补充协议场景下一律剔除
        findings = [f for f in findings if not f.missing]

    result = ReviewResult(
        path=p,
        text=text,
        mode=mode,
        summary=summary,
        findings=_sort_findings(findings),
        checks=checks,
        questions=_build_questions(checks, llm_result.questions, supplement=is_supplement, text=text),
        llm_error=llm_result.error,
        reviewed_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        supplement=is_supplement,
        supplement_evidence=evidence,
        notes=notes,
        meta={
            "our_companies": list(settings.our_companies),
            "our_locations": list(settings.our_locations),
            "char_count": len(text),
            "penalty_max_pct": settings.contract_penalty_max_pct,
        },
    )
    return result
