"""审批「用印申请」对接的测试：表单解析、合同判定、附件下载、评论拼装、事件处理。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402
from src.contract import approval  # noqa: E402
from src.contract.bot import ContractBot  # noqa: E402
from src.contract.store import ContractStore  # noqa: E402

SAMPLE = ROOT / "data" / "sample_contract.txt"


def _form_widgets(name="合同附件", ext="'采购合同.txt'", url="https://example.com/sign/abc"):
    return [
        {"id": "w1", "name": "是否合同文件", "type": "checkbox", "value": ["是"]},
        {"id": "w2", "name": name, "type": "attachmentV2", "ext": ext, "value": [url]},
    ]


class FakeAPI:
    def __init__(self, instance=None, comment_error: str = ""):
        self.instance = instance or {
            "approval_code": "C1",
            "approval_name": "用印申请",
            "instance_code": "I1",
            "user_id": "ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "status": "PENDING",
            "serial_number": "20261112001",
            "form": json.dumps(_form_widgets(), ensure_ascii=False),
            "task_list": [
                {"status": "PENDING", "open_id": "ou_approver"},
                {"status": "APPROVED", "open_id": "ou_other"},
            ],
        }
        self.comments: list[tuple[str, str]] = []
        self.comment_users: list[tuple[str, str]] = []
        self.cards: list[tuple[str, dict]] = []
        self.comment_error = comment_error

    def get_instance(self, instance_code: str):
        if instance_code != self.instance["instance_code"]:
            raise AssertionError(f"unexpected instance {instance_code}")
        return self.instance

    def download_url(self, url: str, out_path: Path) -> None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SAMPLE, out_path)

    def create_comment(
        self, instance_code: str, text: str, at_open_ids=None, user_id="", user_id_type="open_id"
    ) -> str:
        if self.comment_error:
            raise RuntimeError(self.comment_error)
        self.comments.append((instance_code, text))
        self.comment_users.append((user_id, user_id_type))
        return "cmt_001"

    def send_card_to_open_id(self, open_id: str, card: dict) -> None:
        self.cards.append((open_id, card))


def make_settings(tmp: Path, **overrides) -> Settings:
    data = dict(
        app_id="cli_test",
        app_secret="secret",
        our_companies=["珠海示例制造"],
        our_locations=["珠海"],
        llm_mode="mock",
        db_path=str(tmp / "db.sqlite"),
        contract_out_dir=str(tmp / "out"),
        contract_upload_dir=str(tmp / "uploads"),
        contract_approval_codes=["C1"],
        contract_comment_enabled=True,
    )
    data.update(overrides)
    return Settings(**data)


class ApprovalFormTest(unittest.TestCase):
    def test_parse_form_string_list_dict_empty(self):
        widgets = _form_widgets()
        self.assertEqual(len(approval.parse_form(json.dumps(widgets))), 2)
        self.assertEqual(len(approval.parse_form(widgets)), 2)
        self.assertEqual(len(approval.parse_form(widgets[0])), 1)
        self.assertEqual(approval.parse_form(""), [])
        self.assertEqual(approval.parse_form("不是JSON"), [])

    def test_is_contract_positive(self):
        ok, reason = approval.is_contract(_form_widgets())
        self.assertTrue(ok)
        self.assertIn("是", reason)

    def test_is_contract_negative(self):
        widgets = [{"name": "是否合同文件", "type": "checkbox", "value": ["否"]}]
        ok, reason = approval.is_contract(widgets)
        self.assertFalse(ok)
        self.assertIn("非合同", reason)

    def test_is_contract_without_widget(self):
        ok, reason = approval.is_contract([{"name": "事由", "type": "text", "value": "盖章"}])
        self.assertFalse(ok)
        self.assertIn("没有找到", reason)

    def test_find_attachments(self):
        files = approval.find_attachments(_form_widgets())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["name"], "采购合同.txt")
        self.assertTrue(files[0]["url"].startswith("http"))

    def test_extract_instance_code(self):
        self.assertEqual(
            approval.extract_instance_code(
                "https://applink.feishu.cn/client/approval/detail?instance_code=ABC123&x=1"
            ),
            "ABC123",
        )
        uuid = "6f8d3c1e-1111-2222-3333-444455556666"
        self.assertEqual(approval.extract_instance_code(f"实例 {uuid}"), uuid)
        self.assertEqual(approval.extract_instance_code("  plain-code "), "plain-code")

    def test_build_comment_from_record(self):
        record = {
            "file_name": "供货框架协议.docx",
            "overall": "高",
            "findings_json": json.dumps(
                [
                    {"level": "high", "title": "付款进入个人账户", "issue": "存在资金风险。细节略", "quote": "收款账号：张三 6222"},
                    {"level": "medium", "title": "缺少违约金上限", "issue": "", "quote": "每逾期一日 1%"},
                ],
                ensure_ascii=False,
            ),
            "questions_json": json.dumps(["是否含税", "发票类型", "账户变更流程"], ensure_ascii=False),
            "summary_json": json.dumps({"amount": "¥1,200,000.00", "counterparty": "乙方：示例贸易"}, ensure_ascii=False),
        }
        text = approval.build_comment_from_record(record, {"approval_name": "用印申请"})
        self.assertLessEqual(len(text.splitlines()), 12)
        self.assertIn("【AI 合同预审】", text)
        # 结论行必须给一句能照做的判断，不是只报计数
        self.assertIn("结论：建议先确认再用印", text)
        self.assertIn("高 1 / 中 1 / 低 0", text)
        self.assertIn("先确认：付款进入个人账户", text)
        self.assertIn("付款进入个人账户", text)
        self.assertNotIn("原文：", text)  # 极简版不带原文引用
        self.assertIn("签约前必问", text)

    def test_verdict_blocks_when_two_high(self):
        record = {
            "file_name": "x.docx",
            "overall": "高",
            "findings_json": json.dumps(
                [{"level": "high", "title": f"高风险{i}", "issue": "x", "quote": "原文引用足够长的一段"}
                 for i in range(2)]
                + [{"level": "medium", "title": "一个中风险", "issue": "y", "quote": "原文引用足够长的一段"}],
                ensure_ascii=False,
            ),
            "questions_json": json.dumps([], ensure_ascii=False),
            "summary_json": json.dumps({}, ensure_ascii=False),
        }
        text = approval.build_comment_from_record(record, {"approval_name": "用印申请"})
        self.assertIn("结论：暂缓用印", text)

    def test_verdict_accepts_when_nothing_serious(self):
        record = {
            "file_name": "x.docx",
            "overall": "低",
            "findings_json": json.dumps(
                [{"level": "low", "title": "小问题", "issue": "x", "quote": "原文引用足够长的一段"}],
                ensure_ascii=False,
            ),
            "questions_json": json.dumps([], ensure_ascii=False),
            "summary_json": json.dumps({}, ensure_ascii=False),
        }
        text = approval.build_comment_from_record(record, {"approval_name": "用印申请"})
        self.assertIn("结论：可按常规用印", text)

    def test_form_consistency(self):
        contract = "甲方 LEGITCHECK LIMITED COMPANY 与乙方签订。\n签订日期：2026年10月26日，合同结束日期 2027-10-25，期满自动顺延。"
        widgets = [
            {"name": "对方单位名称", "type": "input", "value": "LEGITCHECK LIMITED COMPANY"},
            {"name": "签订日期", "type": "date", "value": "1794474457000"},  # 故意错的
            {"name": "合同自动顺延", "type": "radioV2", "value": "是"},
        ]
        notes = approval.form_consistency(widgets, contract)
        self.assertEqual(len(notes), 1)
        self.assertIn("签订日期", notes[0])
        ok_widgets = [
            {"name": "对方单位名称", "type": "input", "value": "LEGITCHECK LIMITED COMPANY"},
            {"name": "合同自动顺延", "type": "radioV2", "value": "是"},
        ]
        self.assertEqual(approval.form_consistency(ok_widgets, contract), ["表单关键字段与合同正文一致"])


class ApprovalProcessTest(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="approval-test-", dir=str(ROOT / "out")))
        self.settings = make_settings(self.tmp)
        self.store = ContractStore(self.settings.db_file)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_process_instance_ok(self):
        api = FakeAPI()
        result = approval.process_instance(api, self.settings, self.store, "I1")
        self.assertTrue(result["ok"])
        self.assertFalse(result["skipped"])
        self.assertEqual(result["comment_id"], "cmt_001")
        self.assertEqual(len(api.comments), 1)
        self.assertIn("采购合同", api.comments[0][1])
        record = self.store.get(result["results"][0]["review_id"])
        self.assertEqual(record["overall"], "高")
        self.assertEqual(record["message_id"], "I1")
        for key in ("markdown", "json"):
            self.assertTrue(Path(result["results"][0]["reports"][key]).exists())

    def test_process_instance_skip_non_contract(self):
        form = [{"name": "是否合同文件", "type": "checkbox", "value": ["否"]}]
        api = FakeAPI(instance={"instance_code": "I1", "form": json.dumps(form, ensure_ascii=False), "task_list": []})
        result = approval.process_instance(api, self.settings, self.store, "I1")
        self.assertTrue(result["skipped"])
        self.assertFalse(result["ok"])
        self.assertEqual(api.comments, [])

    def test_process_instance_no_attachment(self):
        form = [{"name": "是否合同文件", "type": "checkbox", "value": ["是"]}]
        api = FakeAPI(instance={"instance_code": "I1", "form": json.dumps(form, ensure_ascii=False), "task_list": []})
        result = approval.process_instance(api, self.settings, self.store, "I1")
        self.assertTrue(result["skipped"])
        self.assertIn("附件", result["reason"])

    def test_comment_failure_does_not_raise(self):
        api = FakeAPI(comment_error="权限不足")
        result = approval.process_instance(api, self.settings, self.store, "I1")
        self.assertTrue(result["ok"])
        self.assertIn("评论写入失败", result["comment"])

    def test_process_instance_uses_open_id_for_comment(self):
        api = FakeAPI()
        api.instance["open_id"] = "ou_creator"
        result = approval.process_instance(api, self.settings, self.store, "I1")
        self.assertEqual(result["comment_id"], "cmt_001")

    def test_placeholder_comment_before_ocr(self):
        """扫描件先发占位评论，避免审批人看到空评论直接批单。"""
        api = FakeAPI()
        with mock.patch.object(approval.ocr_mod, "needs_ocr", return_value=True), \
                mock.patch.object(approval.ocr_mod, "page_count", return_value=16):
            result = approval.process_instance(api, self.settings, self.store, "I1")
        self.assertTrue(result["ok"])
        self.assertEqual(len(api.comments), 2)
        self.assertIn("正在识别扫描件", api.comments[0][1])
        self.assertIn("16 页", api.comments[0][1])
        self.assertNotIn("正在识别扫描件", api.comments[1][1])
        self.assertTrue(result["placeholder_id"])

    def test_no_placeholder_when_text_pdf(self):
        """文字型 PDF 不发占位评论，避免多一条噪音。"""
        api = FakeAPI()
        with mock.patch.object(approval.ocr_mod, "needs_ocr", return_value=False):
            result = approval.process_instance(api, self.settings, self.store, "I1")
        self.assertEqual(len(api.comments), 1)
        self.assertEqual(result["placeholder_id"], "")

    def test_no_placeholder_when_ocr_disabled(self):
        api = FakeAPI()
        self.settings.contract_ocr_enabled = False
        with mock.patch.object(approval.ocr_mod, "needs_ocr", return_value=True):
            approval.process_instance(api, self.settings, self.store, "I1")
        self.assertEqual(len(api.comments), 1)

    def test_yongyin_form_always_contract(self):
        """真实用印申请表单：无「是否合同」勾选题，靠审批名判定。"""
        form = [
            {"name": "合同類型", "type": "radioV2", "value": ["采购"]},
            {"name": "自編合同編號", "type": "input", "value": "HT-2026-001"},
            {"name": "用印事由", "type": "input", "value": "与示例贸易签采购合同"},
            {"name": "签订日期", "type": "date", "value": "1762886400000"},
            {"name": "我方簽署單位", "type": "radioV2", "value": ["珠海示例制造"]},
        ]
        ok, reason = approval.is_contract(form, approval_name="用印申请")
        self.assertTrue(ok)
        self.assertIn("合同类审批", reason)
        # 勾选题缺失且审批名不是合同类 → 跳过
        ok2, _ = approval.is_contract(form, approval_name="请假申请")
        self.assertFalse(ok2)
        # 「合同類型/編號」这类字段不会被误当勾选项
        self.assertIsNone(approval.find_contract_widget(form))

    def test_process_instance_textarea_fallback(self):
        text = SAMPLE.read_text("utf-8")
        form = [
            {"name": "是否合同", "type": "input", "value": "是"},
            {"name": "合同正文", "type": "textarea", "value": text},
        ]
        api = FakeAPI(
            instance={
                "instance_code": "I1",
                "open_id": "ou_creator",
                "form": json.dumps(form, ensure_ascii=False),
                "task_list": [],
            }
        )
        result = approval.process_instance(api, self.settings, self.store, "I1")
        self.assertTrue(result["ok"])
        self.assertEqual(len(api.comments), 1)
        self.assertTrue(Path(result["results"][0]["reports"]["json"]).exists())


class ApprovalEventTest(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="approval-bot-", dir=str(ROOT / "out")))
        self.settings = make_settings(self.tmp)
        self.api = FakeAPI()
        self.store = ContractStore(self.settings.db_file)
        self.bot = ContractBot(self.settings, api=self.api, store=self.store)
        # 生产环境预审跑在后台线程；测试里同步执行，断言才稳定
        self.bot.approval_async = False

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _event(status="PENDING", approval_code="C1", instance_code="I1"):
        return SimpleNamespace(
            event=SimpleNamespace(status=status, approval_code=approval_code, instance_code=instance_code)
        )

    def test_pending_event_comments_only_by_default(self):
        """默认不私聊通知审批人（2026-11-14 用户拍板：只在审批单里写评论）。"""
        self.bot.on_approval_event(self._event())
        self.assertEqual(len(self.api.comments), 1)
        self.assertEqual(self.api.cards, [])
        self.assertEqual(len(self.store.recent()), 1)

    def test_pending_event_notifies_when_enabled(self):
        self.settings.contract_notify_approvers = True
        self.bot.on_approval_event(self._event())
        self.assertEqual(len(self.api.comments), 1)
        self.assertEqual(self.api.cards[0][0], "ou_approver")
        self.assertEqual(len(self.store.recent()), 1)

    def test_non_pending_event_ignored(self):
        self.bot.on_approval_event(self._event(status="APPROVED"))
        self.assertEqual(self.api.comments, [])
        self.assertEqual(self.store.recent(), [])

    def test_other_approval_code_ignored(self):
        self.bot.on_approval_event(self._event(approval_code="OTHER"))
        self.assertEqual(self.api.comments, [])

    def test_comment_disabled(self):
        self.settings.contract_comment_enabled = False
        self.bot.on_approval_event(self._event())
        self.assertEqual(self.api.comments, [])
        self.assertEqual(len(self.store.recent()), 1)

    def test_comment_prefers_applicant(self):
        """用户拍板：评论优先署名申请人（读起来像"提交时附带的 AI 预审"）。"""
        self.settings.contract_comment_open_id = "ou_bot_admin"
        self.bot.on_approval_event(self._event())
        self.assertEqual(len(self.api.comments), 1)
        self.assertEqual(self.api.comment_users[0][0], "ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")

    def test_comment_falls_back_to_configured_author(self):
        """申请人写不进去时（如跨租户/非参与人），退回配置的管理员。"""
        self.settings.contract_comment_open_id = "ou_bot_admin"
        api = self.api
        calls: list[tuple[str, str]] = []

        def fake_comment(instance_code, text, at_open_ids=None, user_id="", user_id_type="open_id"):
            calls.append((user_id, user_id_type))
            if user_id == "ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx":
                raise RuntimeError("飞书 API 错误 60009: user has no permission")
            api.comments.append((instance_code, text))
            return "cmt_002"

        api.create_comment = fake_comment  # type: ignore[method-assign]
        self.bot.on_approval_event(self._event())
        self.assertEqual(len(api.comments), 1)
        self.assertEqual(calls[-1], ("ou_bot_admin", "open_id"))

    def test_comment_falls_back_to_approver_when_all_else_fails(self):
        """申请人和配置的人都不行时，最后试当前待办审批人。"""
        self.settings.contract_comment_open_id = "ou_bot_admin"

        def fake_comment(instance_code, text, at_open_ids=None, user_id="", user_id_type="open_id"):
            if user_id in ("ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "ou_bot_admin"):
                raise RuntimeError("飞书 API 错误 60009: user has no permission")
            self.api.comments.append((instance_code, text))
            self.api.comment_users.append((user_id, user_id_type))
            return "cmt_003"

        self.api.create_comment = fake_comment  # type: ignore[method-assign]
        self.bot.on_approval_event(self._event())
        self.assertEqual(len(self.api.comments), 1)


if __name__ == "__main__":
    unittest.main()
