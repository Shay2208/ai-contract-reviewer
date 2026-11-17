"""合同预审模块测试：解析、清单、规则审查、报告落盘。"""

from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from src.config import Settings
from src.contract.checklist import run_checks
from src.contract.parser import extract_text
from src.contract.render import render_markdown, write_report
from src.contract.reviewer import _find_amount, quote_in_text, review_contract

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_TXT = ROOT / "data" / "sample_contract.txt"
SAMPLE_DOCX = ROOT / "data" / "sample_contract.docx"


def _settings(**overrides) -> Settings:
    settings = Settings()
    settings.our_companies = ["珠海示例制造", "示例集团", "示例物流"]
    settings.our_locations = ["珠海"]
    settings.llm_mode = "mock"  # 测试默认走规则，不打外部接口
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


class ContractParserTest(unittest.TestCase):
    def test_txt(self):
        text = extract_text(SAMPLE_TXT)
        self.assertIn("设备采购合同", text)
        self.assertIn("1,200,000.00", text)
        self.assertGreater(len(text), 300)

    def test_docx(self):
        text = extract_text(SAMPLE_DOCX)
        self.assertIn("设备采购合同", text)
        self.assertIn("珠海示例制造有限公司", text)

    def test_unsupported(self):
        from src.contract.parser import ParseError

        with self.assertRaises(ParseError):
            extract_text(ROOT / "data" / "mock_chats.json")


class ContractChecklistTest(unittest.TestCase):
    def setUp(self):
        self.text = extract_text(SAMPLE_TXT)
        self.results = {r.item.id: r for r in run_checks(self.text)}

    def test_missing_deemed_pass(self):
        self.assertTrue(self.results["deemed_pass"].missing)

    def test_missing_bank_account(self):
        self.assertTrue(self.results["bank_account"].missing)

    def test_missing_parties_credit_code(self):
        self.assertTrue(self.results["parties"].missing)

    def test_missing_tax(self):
        self.assertTrue(self.results["tax_invoice"].missing)

    def test_present_payment_terms(self):
        self.assertFalse(self.results["payment_terms"].missing)

    def test_present_dispute(self):
        self.assertFalse(self.results["dispute"].missing)


class ContractReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = _settings()
        cls.result = review_contract(SAMPLE_TXT, cls.settings, force_rules=True)

    def test_overall_high(self):
        self.assertEqual(self.result.overall, "高")
        self.assertTrue(self.result.high)

    def test_conflict_finding(self):
        titles = {f.title for f in self.result.findings}
        self.assertIn("仲裁与诉讼同时约定", titles)

    def test_daily_penalty_finding(self):
        titles = {f.title for f in self.result.findings}
        self.assertIn("逾期违约金按日计算过高", titles)

    def test_personal_account_finding(self):
        titles = {f.title for f in self.result.findings}
        self.assertIn("付款可能进入个人账户", titles)

    def test_jurisdiction_not_flagged(self):
        # 合同写的是「甲方所在地」（我方是甲方），不应报管辖地风险
        titles = {f.title for f in self.result.findings}
        self.assertNotIn("争议解决地不在我方所在地", titles)

    def test_amount_parsed(self):
        text = extract_text(SAMPLE_TXT)
        quote, value = _find_amount(text)
        self.assertAlmostEqual(value, 1_200_000.0)
        self.assertIn("1,200,000", quote)

    def test_summary(self):
        self.assertEqual(self.result.summary["contract_type"], "采购")
        self.assertIn("珠海示例制造", self.result.summary["our_party"])
        self.assertIn("1,200,000", self.result.summary["amount"])

    def test_questions(self):
        self.assertTrue(self.result.questions)
        self.assertTrue(any("含税" in q for q in self.result.questions))

    def test_quote_verification(self):
        self.assertTrue(quote_in_text("收款账户：户名 张三（个人账户", self.result.text))
        self.assertFalse(quote_in_text("本合同约定的违约金为合同总额的百分之五十", self.result.text))

    def test_every_text_finding_has_quote(self):
        for finding in self.result.findings:
            if finding.source == "rule" and finding.quote:
                self.assertTrue(
                    quote_in_text(finding.quote, self.result.text),
                    f"引用回验失败：{finding.title} / {finding.quote}",
                )

    def test_markdown_contains_sections(self):
        md = render_markdown(self.result)
        for section in ("## 一、合同梗概", "## 二、风险清单", "## 三、检查清单", "## 五、签约前必问对方的问题"):
            self.assertIn(section, md)

    def test_write_report(self):
        (ROOT / "out").mkdir(parents=True, exist_ok=True)
        tmp = ROOT / "out" / f"_test_contract_{uuid.uuid4().hex[:8]}"
        try:
            paths = write_report(self.result, tmp)
            self.assertTrue(paths["markdown"].exists())
            self.assertTrue(paths["json"].exists())
            self.assertIn("docx", paths)
            self.assertGreater(paths["docx"].stat().st_size, 5000)
            data = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(data["overall_risk"], "高")
            self.assertTrue(data["findings"])
            self.assertEqual(len(data["checklist"]), 31)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
