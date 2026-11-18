"""原件质量问题降级 + 审批评论全量列出（2026-11-18 两项改动）。

口径（行政同事反馈）：
- 「OCR 乱码」「条款编号混乱」是文件本身的毛病，不是合同风险——
  不进风险榜、不参与定级、不上卡片，只在评论/报告末尾压一行提示。
- 评论区要能看全：全量列出所有风险（实测飞书评论 10000 字可写、
  12000 字报 4002），超长由 split_comment 拆成多条。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.contract import approval, cards
from src.contract.models import Finding, ReviewResult
from src.contract.quality import is_text_quality
from src.contract.reviewer import _sort_findings


def _finding(**kw) -> Finding:
    base = dict(level="medium", title="标题", issue="说明", quote="原文引用内容")
    base.update(kw)
    return Finding(**base)


def _record(findings: list[dict]) -> dict:
    return {
        "file_name": "测试合同.pdf",
        "overall": "高",
        "findings_json": json.dumps(findings, ensure_ascii=False),
        "questions_json": json.dumps([], ensure_ascii=False),
        "summary_json": json.dumps({}, ensure_ascii=False),
    }


class IsTextQualityTest(unittest.TestCase):
    def test_ocr_and_numbering_are_quality(self):
        self.assertTrue(is_text_quality({"title": "大量OCR乱码错字", "issue": "x"}))
        self.assertTrue(is_text_quality({"title": "条款编号严重混乱", "issue": ""}))
        self.assertTrue(is_text_quality({"title": "扫描件字迹模糊", "issue": ""}))

    def test_real_risk_not_flagged(self):
        self.assertFalse(is_text_quality({"title": "乙方免责范围过宽", "issue": "排除间接损失"}))

    def test_quote_mentioning_ocr_is_evidence_not_conclusion(self):
        # 引用原文里出现"乱码"是证据，不能据此把整条降级
        self.assertFalse(
            is_text_quality({"title": "关键条款表述不清", "issue": "", "quote": "原文有乱码字样"})
        )

    def test_finding_property_and_dict(self):
        f = _finding(title="条款编号混乱")
        self.assertTrue(f.text_quality)
        self.assertTrue(f.to_dict()["text_quality"])
        g = _finding(title="违约金过高")
        self.assertFalse(g.text_quality)
        self.assertFalse(g.to_dict()["text_quality"])


class ResultCountsTest(unittest.TestCase):
    def _result(self, findings: list[Finding]) -> ReviewResult:
        return ReviewResult(path=Path("x.pdf"), text="", mode="rules", findings=findings)

    def test_quality_excluded_from_counts_and_overall(self):
        result = self._result(
            [
                _finding(level="high", title="乙方免责范围过宽"),
                _finding(level="high", title="大量OCR乱码错字"),
                _finding(level="medium", title="条款编号严重混乱"),
            ]
        )
        self.assertEqual(len(result.high), 1)
        self.assertEqual(len(result.medium), 0)
        self.assertEqual(len(result.quality_findings), 2)
        # 1 条高风险 → 中高，而不是被质量条目抬成「高」（需 2 条高风险）
        self.assertEqual(result.overall, "中高")

    def test_sort_puts_quality_last(self):
        findings = [
            _finding(level="low", title="正常低风险"),
            _finding(level="high", title="条款编号严重混乱"),
            _finding(level="medium", title="正常中风险"),
        ]
        ordered = _sort_findings(findings)
        self.assertEqual(ordered[-1].title, "条款编号严重混乱")
        self.assertEqual(ordered[0].level, "medium")


class CardTest(unittest.TestCase):
    def test_review_card_hides_quality_items(self):
        record = {
            "file_name": "x.pdf",
            "overall": "高",
            "high": 1,
            "medium": 1,
            "low": 0,
            "summary": {},
            "findings": [
                {"level": "high", "title": "付款进入个人账户", "quote": "收款账号：张三"},
                {"level": "medium", "title": "大量OCR乱码错字", "quote": ""},
            ],
        }
        card = json.dumps(cards.review_card(record), ensure_ascii=False)
        self.assertIn("付款进入个人账户", card)
        self.assertNotIn("OCR", card)


class FullCommentTest(unittest.TestCase):
    def _many(self, count: int) -> dict:
        findings = [
            {
                "level": "high" if i < 3 else "medium",
                "title": f"风险条目{i}",
                "issue": f"这是第{i}条风险的后果说明",
                "quote": f"第{i}条原文引用内容足够长",
            }
            for i in range(count)
        ]
        return _record(findings)

    def test_full_lists_every_finding(self):
        text = approval.build_comment_from_record(self._many(8), {"approval_name": "用印申请"})
        for i in range(8):
            self.assertIn(f"风险条目{i}", text)
        self.assertIn("8. [中] 风险条目7", text)
        self.assertGreater(len(text.splitlines()), 8)

    def test_full_false_keeps_top_items(self):
        text = approval.build_comment_from_record(
            self._many(8), {"approval_name": "用印申请"}, full=False
        )
        self.assertIn("风险条目3", text)
        self.assertNotIn("风险条目4", text)
        self.assertLessEqual(len(text.splitlines()), 12)

    def test_quality_line_appended_and_risks_clean(self):
        findings = [
            {"level": "high", "title": "付款账户风险", "issue": "x", "quote": "收款账号：张三"},
            {"level": "medium", "title": "条款编号严重混乱", "issue": "y", "quote": ""},
        ]
        text = approval.build_comment_from_record(_record(findings), {"approval_name": "用印申请"})
        self.assertIn("付款账户风险", text)
        # 质量问题不能作为编号风险行出现，只允许在「原件质量」提示行里点名
        self.assertNotIn(". [中] 条款编号严重混乱", text)
        self.assertIn("原件质量：存在 1 处文件问题", text)


class SplitCommentTest(unittest.TestCase):
    def test_short_stays_single(self):
        text = "【AI 合同预审】甲｜乙\n结论：可按常规用印（高 0 / 中 0 / 低 0）"
        self.assertEqual(approval.split_comment(text), [text])
        self.assertEqual(approval.split_comment(""), [])

    def test_long_breaks_into_chunks(self):
        lines = ["【AI 合同预审】用印申请｜长合同.pdf"] + [
            f"{i}. [高] 第{i}条风险｜原文「{'引' * 80}」｜解释：{'解' * 60}"
            for i in range(1, 61)
        ]
        text = "\n".join(lines)
        self.assertGreater(len(text), 8000)
        chunks = approval.split_comment(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 8000)
        self.assertIn("（1/", chunks[0])
        self.assertIn("（续 2/", chunks[1])
        # 拆分不能丢内容
        self.assertIn("第60条风险", chunks[-1])


if __name__ == "__main__":
    unittest.main()
