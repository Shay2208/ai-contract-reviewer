"""数据模型：一条风险结论（Finding）与一次审查结果（ReviewResult）。

这里只回答两个问题：「一条结论长什么样」「综合风险怎么定」。
规则的判定逻辑在 rules/ 包里，编排流程在 reviewer.py 里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .checklist import HIGH, LEVEL_LABEL, LOW, MEDIUM, CheckResult, missing_items

# 排序用：高 → 中 → 低
LEVEL_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}

__all__ = [
    "Finding",
    "ReviewResult",
    "LEVEL_ORDER",
    "LEVEL_LABEL",
    "HIGH",
    "MEDIUM",
    "LOW",
]


@dataclass
class Finding:
    level: str
    title: str
    clause: str = ""
    quote: str = ""
    issue: str = ""
    suggestion: str = ""
    confidence: float = 1.0
    source: str = "rule"  # rule | llm
    missing: bool = False  # 整条条款缺失（没有原文可引）
    rule: str = ""  # 高危模式编号（R1–R17），仅第二轮收敛时填

    @property
    def level_label(self) -> str:
        return LEVEL_LABEL.get(self.level, self.level)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "level_label": self.level_label,
            "title": self.title,
            "clause": self.clause,
            "quote": self.quote,
            "issue": self.issue,
            "suggestion": self.suggestion,
            "confidence": round(self.confidence, 2),
            "source": self.source,
            "missing": self.missing,
            "rule": self.rule,
        }


@dataclass
class ReviewResult:
    path: Path
    text: str
    mode: str  # rules | llm
    summary: dict = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    llm_error: str = ""
    reviewed_at: str = ""
    meta: dict = field(default_factory=dict)
    supplement: bool = False  # 补充/变更/续签协议：缺失条款依主协议执行，不计风险
    supplement_evidence: str = ""
    notes: list[str] = field(default_factory=list)  # 不参与定级的提示（如"未约定事项依主协议执行"）

    @property
    def substantive(self) -> list[Finding]:
        """有原文依据的风险（不含"整条缺失"那类）。"""
        return [f for f in self.findings if not f.missing and (f.quote or "").strip()]

    @property
    def high(self) -> list[Finding]:
        return [f for f in self.findings if f.level == HIGH]

    @property
    def medium(self) -> list[Finding]:
        return [f for f in self.findings if f.level == MEDIUM]

    @property
    def low(self) -> list[Finding]:
        return [f for f in self.findings if f.level == LOW]

    @property
    def overall(self) -> str:
        # 综合风险只由「有原文依据的风险」决定：
        # 「缺少某条款」是清单体检的结果，不同合同类型（尤其补充协议）缺失是正常的，
        # 让它参与定级会把每份合同都算成"高"。
        basis = self.substantive or self.findings
        highs = [f for f in basis if f.level == HIGH]
        mids = [f for f in basis if f.level == MEDIUM]
        if len(highs) >= 2:
            return "高"
        if highs:
            return "中高"
        if len(mids) >= 3:
            return "中"
        if mids:
            return "中低"
        return "低"

    def to_dict(self) -> dict:
        return {
            "file": self.path.name,
            "path": str(self.path),
            "reviewed_at": self.reviewed_at,
            "mode": self.mode,
            "overall_risk": self.overall,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "missing": [
                {"id": c.item.id, "group": c.item.group, "title": c.item.title,
                 "level": c.item.risk, "suggestion": c.item.suggestion}
                for c in missing_items(self.checks)
            ],
            "checklist": [
                {"id": c.item.id, "group": c.item.group, "title": c.item.title,
                 "status": c.status, "evidence": c.evidence, "level": c.item.risk}
                for c in self.checks
            ],
            "questions": self.questions,
            "llm_error": self.llm_error,
            "supplement": self.supplement,
            "supplement_evidence": self.supplement_evidence,
            "notes": self.notes,
            "meta": self.meta,
        }
