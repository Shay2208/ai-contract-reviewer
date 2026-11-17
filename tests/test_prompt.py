"""单轮提示词：发散 + 高危清单合并成一次调用（2026-11-16 用户要求改成单轮）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.contract import llm_reviewer  # noqa: E402
from src.contract.llm_reviewer import SYSTEM_PROMPT, _clean_findings, llm_review  # noqa: E402


def _settings():
    """只用 llm_review 需要的几个字段，避免依赖完整 Settings。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        our_companies=["示例物流（珠海）有限公司"],
        our_locations=["珠海"],
        llm_timeout_review=180,
    )


class SystemPromptTest(unittest.TestCase):
    def test_prompt_keeps_free_divergence(self):
        self.assertIn("自由发散", SYSTEM_PROMPT)
        self.assertIn("不要受任何固定清单限制", SYSTEM_PROMPT)
        self.assertIn("宁可多报", SYSTEM_PROMPT)

    def test_prompt_carries_high_risk_checklist(self):
        for code in ("R1", "R7", "R11", "R12", "R13", "R17"):
            self.assertIn(code, SYSTEM_PROMPT)
        self.assertIn("清单不是穷举", SYSTEM_PROMPT)

    def test_prompt_asks_for_rule_field(self):
        self.assertIn('"rule"', SYSTEM_PROMPT)

    def test_two_pass_prompts_are_gone(self):
        self.assertFalse(hasattr(llm_reviewer, "HIGH_RISK_PROMPT"))
        self.assertFalse(hasattr(llm_reviewer, "llm_high_risk"))


class CleanFindingsTest(unittest.TestCase):
    def test_keeps_rule_code(self):
        got = _clean_findings(
            [
                {
                    "level": "high",
                    "title": "个人账户收款",
                    "quote": "户名：张三 账号：6222****1234",
                    "issue": "货款打进个人账户，出问题难以追讨",
                    "rule": "r11",
                }
            ]
        )
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["rule"], "R11")

    def test_rule_defaults_to_empty(self):
        got = _clean_findings(
            [{"level": "medium", "title": "付款节点不明", "quote": "验收合格后支付剩余款项"}]
        )
        self.assertEqual(got[0]["rule"], "")


class SingleCallTest(unittest.TestCase):
    """整份审查只调用一次模型。"""

    class FakeLLM:
        available = True

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def chat_json(self, system, user, **kwargs):
            self.calls.append({"system": system, "user": user, "kwargs": kwargs})
            return {
                "summary": {"contract_type": "服务合同"},
                "findings": [
                    {
                        "level": "high",
                        "title": "个人账户收款",
                        "quote": "户名：张三 账号：6222****1234",
                        "issue": "货款打进个人账户，出问题难以追讨",
                        "rule": "R11",
                    }
                ],
                "questions": ["能否改为对公账户？"],
            }

    def test_one_call_only(self):
        llm = self.FakeLLM()
        result = llm_review(
            llm, "甲方委托乙方提供服务，户名：张三 账号：6222****1234。", _settings()
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(result.findings[0]["rule"], "R11")
        # 高危清单在系统提示里，模型一次就能同时做发散与收敛
        self.assertIn("R11", llm.calls[0]["system"])


if __name__ == "__main__":
    unittest.main()
