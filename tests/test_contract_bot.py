"""合同审查机器人测试：消息 → 审查 → 卡片 → 按钮动作（用假飞书 API，不联网）。"""

from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from src.config import Settings
from src.contract.bot import ContractBot
from src.contract.store import ContractStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample_contract.txt"


class FakeAPI:
    def __init__(self, sample: Path) -> None:
        self.sample = sample
        self.cards: list[dict] = []
        self.texts: list[str] = []
        self.uploaded: list[str] = []
        self.sent_files: list[tuple[str, str]] = []

    def reply_card(self, message_id: str, card: dict) -> str:
        self.cards.append(card)
        return "om_reply"

    def reply_text(self, message_id: str, text: str) -> str:
        self.texts.append(text)
        return "om_reply"

    def download_file(self, message_id: str, file_key: str, out_path) -> Path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.sample.read_text(encoding="utf-8"), encoding="utf-8")
        return path

    def upload_file(self, path, file_type: str = "") -> str:
        self.uploaded.append(str(path))
        return "file_key_1"

    def send_file_to_open_id(self, open_id: str, file_key: str) -> str:
        self.sent_files.append((open_id, file_key))
        return "om_file"

    def send_file(self, chat_id: str, file_key: str) -> str:
        self.sent_files.append((chat_id, file_key))
        return "om_file"

    def bot_info(self) -> dict:
        return {"open_id": "ou_bot", "app_name": "合同审查机器人"}


class FakeAuth:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_task(self, summary, description="", due_ts=None, assignee_open_id=""):
        self.calls.append(
            {
                "summary": summary,
                "description": description,
                "assignee": assignee_open_id,
            }
        )
        return {"task": {"url": "https://applink.feishu.cn/client/todo/detail?guid=test"}}


class ContractBotTest(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(parents=True, exist_ok=True)
        self.tmp = ROOT / "out" / f"_test_bot_{uuid.uuid4().hex[:8]}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(
            app_id="cli_test",
            app_secret="secret",
            llm_mode="mock",
            our_companies=["珠海示例制造", "示例集团", "示例物流"],
            our_locations=["珠海"],
            db_path=str(self.tmp / "db.sqlite"),
            contract_out_dir=str(self.tmp / "out" / "contracts"),
            contract_upload_dir=str(self.tmp / "uploads"),
            lawyer_open_ids=["ou_lawyer"],
            contract_chat_ids=["oc_contract_group"],
        )
        self.api = FakeAPI(SAMPLE)
        self.auth = FakeAuth()
        self.store = ContractStore(self.settings.db_file)
        self.bot = ContractBot(self.settings, api=self.api, store=self.store, auth=self.auth)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _file_message(self, file_name: str = "设备采购合同.txt", chat_type: str = "p2p", chat_id: str = "oc_dm"):
        content = json.dumps({"file_key": "file_1", "file_name": file_name})
        return self.bot.handle_message(
            message_id="om_msg_1",
            chat_id=chat_id,
            chat_type=chat_type,
            sender_open_id="ou_sender",
            message_type="file",
            content=content,
        )

    def test_file_review_returns_card(self):
        result = self._file_message()
        self.assertTrue(result["ok"])
        self.assertEqual(result["overall"], "高")
        self.assertEqual(len(self.api.cards), 1)
        card = self.api.cards[0]
        self.assertIn("审批评论预览", card["header"]["title"]["content"])
        self.assertEqual(card["header"]["template"], "red")
        self.assertNotIn("查看完整报告", json.dumps(card, ensure_ascii=False))

    def test_p2p_card_shows_exact_approval_comment(self):
        """私聊卡片里必须包含与审批评论一字不差的文本（模板评审用）。"""
        self._file_message()
        card = self.api.cards[0]
        divs = [el.get("text", {}).get("content", "") for el in card["elements"] if el.get("tag") == "div"]
        comment = next(d for d in divs if d.strip().startswith("【AI 合同预审】"))
        self.assertIn("结论：", comment)
        self.assertIn("签约前必问：", comment)
        self.assertIn("AI 自动生成，仅供参考，最终以法务意见为准。", comment)
        self.assertLessEqual(len([ln for ln in comment.splitlines() if ln.strip()]), 12)

    def test_group_file_keeps_detailed_card(self):
        """群里仍发详情卡片，不发审批评论预览。"""
        result = self._file_message(chat_type="group", chat_id="oc_contract_group")
        self.assertTrue(result["ok"])
        self.assertIn("合同预审", self.api.cards[0]["header"]["title"]["content"])
        self.assertNotIn("审批评论预览", self.api.cards[0]["header"]["title"]["content"])

    def test_duplicate_event_ignored(self):
        """飞书会重投同一 msg_id：第二次必须直接忽略，不能重复回卡片。"""
        first = self._file_message()
        self.assertTrue(first["ok"])
        second = self._file_message()
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "duplicate")
        self.assertEqual(len(self.api.cards), 1)

    def test_report_files_written(self):
        result = self._file_message()
        review_id = result["review_id"]
        record = self.store.get(review_id)
        self.assertIsNotNone(record)
        self.assertTrue(Path(record["report_md"]).exists())
        self.assertTrue(Path(record["report_json"]).exists())
        self.assertTrue(Path(record["report_docx"]).exists())

    def test_unsupported_format_replies_hint(self):
        result = self._file_message("合同.xlsx")
        self.assertFalse(result["ok"])
        self.assertTrue(any("只认" in t for t in self.api.texts))

    def test_ack_reply_sent_before_review(self):
        """收到文件先回执「正在审查」，不能等结果出来才第一次说话。"""
        result = self._file_message()
        self.assertTrue(result["ok"])
        ack = [t for t in self.api.texts if "已收到" in t and "正在审查" in t]
        self.assertEqual(len(ack), 1)
        self.assertIn("设备采购合同.txt", ack[0])
        # 回执必须发生在卡片之前（先告诉人开始了，再给结果）
        self.assertTrue(self.api.cards, "审查完成后应有结果卡片")

    def test_group_out_of_scope_ignored(self):
        result = self._file_message(chat_type="group", chat_id="oc_other_group")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_in_scope_group")

    def test_group_in_scope_works(self):
        result = self._file_message(chat_type="group", chat_id="oc_contract_group")
        self.assertTrue(result["ok"])

    def test_p2p_text_help_card(self):
        result = self.bot.handle_message(
            message_id="om_text",
            chat_id="oc_dm",
            chat_type="p2p",
            sender_open_id="ou_sender",
            message_type="text",
            content=json.dumps({"text": "你好"}, ensure_ascii=False),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "help")

    def test_card_buttons_are_gone(self):
        """2026-11-14 用户要求去掉卡片按钮：卡片里不再有 action 元素。"""
        self._file_message()
        card = self.api.cards[-1]
        tags = [el.get("tag") for el in card["elements"]]
        self.assertNotIn("action", tags)

    def test_handle_action_disabled(self):
        review_id = self._file_message()["review_id"]
        for action in ("contract_report", "contract_task", "contract_ignore"):
            toast = self.bot.handle_action({"action": action, "review_id": review_id})
            self.assertIn("已停用", toast)
        # 任何动作都不应产生副作用
        self.assertEqual(self.api.uploaded, [])
        self.assertEqual(self.auth.calls, [])
        self.assertEqual(self.store.get(review_id)["status"], "reviewed")

    def test_recent_list(self):
        self._file_message()
        self.assertEqual(len(self.store.recent()), 1)
        self.assertEqual(self.store.stats()["high_risk"], 1)


if __name__ == "__main__":
    unittest.main()
