"""合同 AI 预审：解析 → 清单体检 → 语义审查 → 一页纸报告。

定位：帮业务/法务做「按清单体检」，每条结论都带原文引用和修改建议，
最终由法务拍板，不作为法律意见。
"""

from __future__ import annotations

__all__ = ["review_contract", "extract_text", "ReviewResult"]

from .parser import ParseError, extract_text  # noqa: E402
from .reviewer import ReviewResult, review_contract  # noqa: E402
