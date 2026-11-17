"""2026-11-16 行政视角评审后的回归测试。

行政同事（非法律岗）指着我们的输出说了四个硬伤，全部核对原文成立：

1. 「违约责任不对等」方向反了——合同写的是丙方赔我方全部损失、我方逾期只按日万分之五，
   对我方有利；AI 却报成不对等，还建议「提高甲方违约金比例」（等于主动给自己加责任）。
2. 「缺少逾期未提异议视为合格」是假阳性——合同第三条明明写了「否则视为弃权或认可」。
   根因：规则层生成的缺失项没过「正文是否其实写了」这道闸。
3. 报告说「争议解决地不在我方所在地」——原文是「向甲方所在地法院起诉」，甲方就是我们。
   根因：pypdf 把「甲方所\n地」切断，模型只看到半句。
4. 漏掉金额矛盾：正文 10057，附件合计 8905、含税 8538.3。

这四条都改成了确定性规则/后处理，不依赖模型发挥。
"""

from __future__ import annotations

import unittest

from src.contract import parser, reviewer
from src.contract.checklist import HIGH, CheckItem, CheckResult
from src.contract.reviewer import Finding, _merge, _rule_findings


class _Settings:
    our_companies = ["示例物流（珠海）有限公司"]
    our_locations = ["珠海"]
    contract_penalty_max_pct = 30.0


ANSHI_TEXT = """监控安防工程安装合同
甲方：示例物流（珠海）有限公司
乙方：珠海示例智能科技有限公司
丙方：张某
该工程总造价为 10057 元（大写：壹万零伍拾柒元整）【含税价】。
1、丙方张某系本工程的实际施工人，为本合同项下工程实施的全部义务和责任的唯一承担主体，
2、验收：若甲方有异议，须在验收后 7 个工作日内提出，否则，视为弃权或认可处理。
3、违约责任：丙方逾期完工、质量不合格的，应承担违约金并赔偿甲方损失；
甲方逾期付款的，按日万分之五支付违约金。
丙方应退还已收款项并按合同总金额的 10%向甲方支付违约金。
4、争议：向甲方所在地有管辖权的人民法院提起诉讼。
附件一
一 设备总价： 6,605.00
二 人工： ¥2,300.00
三 工程总造价： ¥8,905.00
"""


class PenaltyDirectionTest(unittest.TestCase):
    """违约金方向：赔给我方的比例不是风险。"""

    def test_penalty_paid_to_us_is_not_reported_as_high(self):
        findings = _rule_findings(ANSHI_TEXT, [], _Settings())
        titles = [f.title for f in findings]
        self.assertFalse(
            any("违约金比例偏高" in t for t in titles),
            f"对方按总金额 10% 赔给我方的条款被误报成偏高：{titles}",
        )

    def test_penalty_payee_detection(self):
        self.assertEqual(reviewer._penalty_payee("按合同总金额的 10%向甲方支付违约金"), "甲方")
        self.assertEqual(reviewer._penalty_payee("应向乙方支付违约金"), "乙方")
        self.assertEqual(reviewer._penalty_payee("没有任何方向"), "")

    def test_harmful_advice_is_dropped(self):
        """建议「提高我方违约金」的模型结论必须被丢弃。"""
        bad = Finding(
            level="high",
            title="违约责任不对等",
            issue="丙方担责而甲方责任轻，不对等。",
            suggestion="提高甲方逾期付款的违约金比例至对等水平。",
            source="llm",
        )
        merged = _merge([], [bad], ANSHI_TEXT, our_side="甲方")
        self.assertEqual(merged, [], "方向相反的『给我方加责任』建议没被拦住")

    def test_advice_about_counterparty_is_kept(self):
        """建议提高对方责任是对的，不能误杀。"""
        good = Finding(
            level="medium",
            title="违约责任不对等",
            issue="丙方违约责任未约定金额。",
            suggestion="提高丙方的违约金并写明计算标准。",
            source="llm",
        )
        merged = _merge([], [good], ANSHI_TEXT, our_side="甲方")
        self.assertEqual(len(merged), 1)


class MissingClauseGateTest(unittest.TestCase):
    """「缺少 XX」必须过正文矛盾校验，且最多 2 条。"""

    def _check(self, title: str) -> list[CheckResult]:
        return [CheckResult(item=CheckItem(id="x", group="权利", title=title, patterns=(), risk=HIGH, suggestion="补"), status="missing", evidence="")]

    def test_deemed_acceptance_not_reported_missing_when_present(self):
        findings = _rule_findings(ANSHI_TEXT, self._check("逾期未提异议视为合格"), _Settings())
        titles = [f.title for f in findings]
        self.assertFalse(
            any("逾期未提异议视为合格" in t and "缺少" in t for t in titles),
            f"合同已写「视为弃权或认可」，仍报缺失：{titles}",
        )

    def test_missing_cap_is_two(self):
        checks = [
            CheckResult(item=CheckItem(id=f"x{i}", group="权利", title=f"条款{i}", patterns=(), risk=HIGH, suggestion="补"),
                        status="missing", evidence="")
            for i in range(6)
        ]
        findings = _rule_findings(ANSHI_TEXT, checks, _Settings())
        missing = [f for f in findings if f.missing]
        self.assertLessEqual(len(missing), reviewer._MISSING_FINDING_CAP)


class AmountConsistencyTest(unittest.TestCase):
    """正文总价 vs 附件合计不一致，必须报出来。"""

    def test_detects_body_vs_attachment_gap(self):
        findings = _rule_findings(ANSHI_TEXT, [], _Settings())
        hit = [f for f in findings if f.title == "合同金额前后不一致"]
        self.assertEqual(len(hit), 1, [f.title for f in findings])
        self.assertIn("10057", hit[0].quote.replace(",", ""))
        self.assertIn("8,905", hit[0].quote)

    def test_no_finding_when_amounts_agree(self):
        text = "合同总金额：10000 元。\n工程总造价：¥10,000.00\n"
        findings = _rule_findings(text, [], _Settings())
        self.assertFalse(any(f.title == "合同金额前后不一致" for f in findings))


class SoftLineJoinTest(unittest.TestCase):
    """PDF 断行必须合并，否则引用被切断、模型编结论。"""

    def test_joins_sentence_split_across_lines(self):
        # 真实 PDF 里的断点就在「甲方所|在地」之间
        raw = "4、争议：向甲方所\n在地有管辖权的人民法院提起诉讼。"
        joined = parser._join_soft_breaks(raw)
        self.assertIn("向甲方所在地有管辖权", joined)

    def test_keeps_clause_and_table_starts(self):
        raw = "上一句没结束\n1、新条款\n二 人工： ¥2,300.00"
        joined = parser._join_soft_breaks(raw)
        self.assertIn("1、新条款", joined)
        self.assertIn("二 人工", joined)

    def test_does_not_join_after_full_stop(self):
        raw = "第一句结束。\n第二句开始。"
        self.assertIn("第一句结束。", parser._join_soft_breaks(raw))


if __name__ == "__main__":
    unittest.main()
