"""补充协议识别、时效检查、以及"明明写了却报缺失"的否决逻辑。"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.contract import kind  # noqa: E402
from src.contract.reviewer import Finding, _contradicted_by_text  # noqa: E402

SUPPLEMENT_TEXT = """《第三方管理系统技术服务协议》之补充协议
鉴于：2021年4月11日，甲乙双方共同签订《三方管理系统技术服务协议》（下称"主协议"）。
一、服务期限的延长：将上述主协议当中的服务期限延长［1年]，即自[2022]年[4]月[18]日起至[2023]年[4]月[17]日止。
二、延长期限内的服务费：服务费为人民币[10000]元/年。按年续费，在每个服务年度开始前提前15个工作日，由甲方支付至乙方的指定如下收款账户：
户名：杭州快驰科技有限公司
开户行：杭州联合农村商业银行股份有限公司吴山支行
账号：201000217982206
三、特别约定：本协议作为对主协议的补充。本协议未约定的，仍由甲乙双方遵照主协议的约定执行。
"""

PLAIN_TEXT = """采购合同
甲方：珠海示例制造科技有限公司
乙方：某某贸易有限公司
合同金额：人民币 100 万元（含税）。
交付时间：合同签订后 30 日内交货到甲方指定仓库。
"""


class DetectSupplementTest(unittest.TestCase):
    def test_by_file_name(self):
        ok, why = kind.detect_supplement("XX合同之补充协议.docx", "无关文本")
        self.assertTrue(ok)
        self.assertIn("补充协议", why)

    def test_traditional_name(self):
        ok, _why = kind.detect_supplement("協議之補充協議.pdf", "")
        self.assertTrue(ok)

    def test_by_text_hints(self):
        ok, why = kind.detect_supplement("scan.pdf", SUPPLEMENT_TEXT)
        self.assertTrue(ok)
        self.assertTrue(why)

    def test_plain_contract_not_supplement(self):
        ok, _why = kind.detect_supplement("采购合同.docx", PLAIN_TEXT)
        self.assertFalse(ok)


class PeriodEndTest(unittest.TestCase):
    def test_parse_bracket_dates(self):
        quote, end, _why = kind.find_period_end(SUPPLEMENT_TEXT)
        self.assertEqual(end, date(2023, 4, 17))
        self.assertIn("2023", quote)

    def test_no_date(self):
        _quote, end, why = kind.find_period_end("本合同没有写期限。")
        self.assertIsNone(end)
        self.assertIn("未", why)

    def test_expired(self):
        self.assertTrue(kind.is_expired(date(2020, 1, 1), today=date(2026, 11, 14)))
        self.assertFalse(kind.is_expired(date(2030, 1, 1), today=date(2026, 11, 14)))
        self.assertFalse(kind.is_expired(None))


class PrepayTest(unittest.TestCase):
    def test_annual_prepay_without_refund(self):
        hit, quote, annual = kind.find_prepay_without_refund(SUPPLEMENT_TEXT)
        self.assertTrue(hit)
        self.assertIn("支付", quote)
        self.assertTrue(annual)

    def test_refund_clause_disables(self):
        text = "合同签订后 7 日内预付全款。若乙方未履约，甲方可要求退款。"
        hit, _quote, _annual = kind.find_prepay_without_refund(text)
        self.assertFalse(hit)


class ContradictionTest(unittest.TestCase):
    def test_account_claim_rejected(self):
        f = Finding(level="high", title="付款账户缺失", issue="未填写具体收款账户信息。", quote="支付至如下账户：")
        self.assertTrue(_contradicted_by_text(f, SUPPLEMENT_TEXT))

    def test_payment_node_claim_rejected(self):
        f = Finding(level="high", title="付款节点缺失", issue="未明确具体付款时间点。", quote="提前15个工作日，由甲方支付")
        self.assertTrue(_contradicted_by_text(f, SUPPLEMENT_TEXT))

    def test_real_absence_kept(self):
        f = Finding(level="medium", title="保密条款缺失", issue="未约定保密义务。", quote="", missing=True)
        self.assertFalse(_contradicted_by_text(f, SUPPLEMENT_TEXT))

    def test_absence_without_evidence_kept(self):
        f = Finding(level="medium", title="验收标准缺失", issue="未约定验收标准。", quote="")
        self.assertFalse(_contradicted_by_text(f, PLAIN_TEXT))


if __name__ == "__main__":
    unittest.main()
