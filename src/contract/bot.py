"""合同审查机器人（飞书长连接版）。

流程：
    审批「用印申请」提交 → 机器人收到事件 → 识别是否合同 → 审查 → 写审批评论
    私聊发合同文件 → 回一张「审批评论预览」卡片（私聊预览，不写审批、不发通知）

卡片不再带按钮（2026-11-14 按用户要求移除）。

不需要公网 IP：飞书官方 SDK 主动连出（长连接模式）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Settings
from ..feishu_api import FeishuAPI, FeishuError
from ..feishu_auth import AuthError, UserAuth
from . import approval, cards
from . import ocr as ocr_mod
from .parser import SUPPORTED, ParseError
from .render import write_report
from .reviewer import review_contract
from .store import ContractStore

log = logging.getLogger("contract-bot")

ALLOWED_SUFFIXES = set(SUPPORTED) | {".doc"}


def _human_size(size: int) -> str:
    if size > 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024:.0f} KB"


def _safe_name(name: str) -> str:
    name = name.replace("\\", "_").replace("/", "_").strip()
    return name or "contract"


class ContractBot:
    def __init__(
        self,
        settings: Settings,
        api: FeishuAPI | None = None,
        store: ContractStore | None = None,
        auth: UserAuth | None = None,
    ) -> None:
        self.settings = settings
        self.api = api or FeishuAPI(settings.app_id, settings.app_secret)
        self.store = store or ContractStore(settings.db_file)
        self.auth = auth or UserAuth(settings)
        self._bot_open_id = ""
        self.message_async = True
        # 飞书事件重试会导致同一 msg_id 被投递多次（审查慢时尤其如此），这里做幂等去重
        self._seen_messages: dict[str, float] = {}
        self._seen_ttl = 900.0

    # ---------- 小工具 ----------
    def bot_open_id(self) -> str:
        if not self._bot_open_id:
            try:
                self._bot_open_id = str(self.api.bot_info().get("open_id", ""))
            except FeishuError as exc:
                log.warning("读取机器人信息失败：%s", exc)
        return self._bot_open_id

    def _download_path(self, file_name: str, message_id: str) -> Path:
        suffix = Path(file_name).suffix.lower()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = f"{stamp}-{_safe_name(Path(file_name).stem)[:40]}{suffix}"
        folder = self.settings.contract_upload_path / time.strftime("%Y-%m")
        folder.mkdir(parents=True, exist_ok=True)
        if message_id:
            base = f"{message_id[-8:]}-{base}"
        return folder / base

    # ---------- 事件：收到消息 ----------
    def on_message(self, event: Any) -> None:
        if self.message_async:
            # 审查要 20-30 秒，同步处理会让飞书判定回调超时并重投事件
            threading.Thread(target=self._on_message_sync, args=(event,), daemon=True).start()
        else:
            self._on_message_sync(event)

    def _on_message_sync(self, event: Any) -> None:
        try:
            data = event.event
            msg = data.message
            sender = getattr(getattr(data, "sender", None), "sender_id", None)
            log.info(
                "收到消息：type=%s chat=%s sender=%s msg_id=%s",
                msg.message_type, msg.chat_id, getattr(sender, "open_id", ""), msg.message_id,
            )
            self.handle_message(
                message_id=msg.message_id,
                chat_id=msg.chat_id,
                chat_type=getattr(msg, "chat_type", "") or "",
                sender_open_id=getattr(sender, "open_id", "") or "",
                message_type=msg.message_type,
                content=msg.content or "{}",
            )
        except Exception:  # noqa: BLE001
            log.exception("处理消息事件失败")

    def handle_message(
        self,
        *,
        message_id: str,
        chat_id: str,
        chat_type: str,
        sender_open_id: str,
        message_type: str,
        content: str,
    ) -> dict[str, Any]:
        """返回处理结果摘要（便于测试；真实事件里异常不外抛）。"""
        if self._is_duplicate(message_id):
            log.info("重复事件已忽略：msg_id=%s", message_id)
            return {"ok": False, "reason": "duplicate"}
        if message_type == "file":
            return self._handle_file(
                message_id=message_id,
                chat_id=chat_id,
                chat_type=chat_type,
                sender_open_id=sender_open_id,
                content=content,
            )
        if message_type == "text":
            text = self._text(content)
            if chat_type == "p2p" or self._mentions_bot(text):
                self.api.reply_card(message_id, cards.help_card())
                return {"ok": True, "action": "help"}
        return {"ok": False, "reason": "unsupported_message_type"}

    def _is_duplicate(self, message_id: str) -> bool:
        """同一 msg_id 在 TTL 内只处理一次（飞书事件重试 / 断线重投）。"""
        if not message_id:
            return False
        now = time.time()
        if len(self._seen_messages) > 500:
            for key, ts in list(self._seen_messages.items()):
                if now - ts > self._seen_ttl:
                    self._seen_messages.pop(key, None)
            if len(self._seen_messages) > 500:  # 仍超量就整体清空，避免无限增长
                self._seen_messages.clear()
        if message_id in self._seen_messages:
            return True
        self._seen_messages[message_id] = now
        return False

    @staticmethod
    def _text(content: str) -> str:
        try:
            return str(json.loads(content or "{}").get("text", "")).strip()
        except (json.JSONDecodeError, AttributeError):
            return ""

    def _mentions_bot(self, text: str) -> bool:
        open_id = self.bot_open_id()
        if not open_id:
            return False
        # 事件里的 @ 已是纯文本，这里只能按「@_user_1」类占位符或机器人名兜底
        return "@_user_" in text

    def _handle_file(
        self,
        *,
        message_id: str,
        chat_id: str,
        chat_type: str,
        sender_open_id: str,
        content: str,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(content or "{}")
        except json.JSONDecodeError:
            return {"ok": False, "reason": "bad_content"}
        file_key = str(payload.get("file_key", "") or "")
        file_name = str(payload.get("file_name", "") or "")
        suffix = Path(file_name).suffix.lower()
        if not file_key:
            return {"ok": False, "reason": "no_file_key"}
        if suffix not in ALLOWED_SUFFIXES:
            self.api.reply_text(
                message_id,
                f"这份文件是 {suffix or '未知格式'}，我暂时只认 Word(.docx)、PDF、txt。\n"
                "如果是扫描件或旧版 .doc，先另存为 .docx 再发我。",
            )
            return {"ok": False, "reason": "unsupported_format", "suffix": suffix}

        # 群聊里只处理指定群（或 @了机器人）的文件，避免把群文件全审一遍
        if chat_type != "p2p" and not self._in_scope(chat_id):
            log.info("群不在审查范围，忽略文件：chat=%s", chat_id)
            return {"ok": False, "reason": "not_in_scope_group"}

        path = self._download_path(file_name, message_id)
        try:
            self.api.download_file(message_id, file_key, path)
        except FeishuError as exc:
            log.warning("下载文件失败：%s", exc)
            self.api.reply_text(message_id, f"文件下载失败（可能缺少 im:resource 权限）：{exc}")
            return {"ok": False, "reason": "download_failed"}

        size = path.stat().st_size if path.exists() else 0
        if size > self.settings.contract_max_mb * 1024 * 1024:
            log.info("文件超限，已回提示：%s %s", path.name, _human_size(size))
            self.api.reply_text(
                message_id,
                f"文件 {_human_size(size)} 超过 {self.settings.contract_max_mb} MB 上限，"
                "可以压缩或拆分后再发。",
            )
            return {"ok": False, "reason": "too_large"}

        # 先立即回执：让发件人知道流程已开始，避免长时间无响应被误以为没收到
        log.info("已收到文件，先回执再审查：%s", path.name)
        self.api.reply_text(
            message_id,
            f"已收到《{path.name}》，正在审查（AI 通读全文识别风险），结果预计 1 分钟内回复，请稍候。",
        )
        try:
            if self.settings.contract_ocr_enabled and ocr_mod.needs_ocr(path):
                pages = ocr_mod.page_count(path) or 1
                minutes = max(1, round(ocr_mod.estimate_seconds(pages) / 60))
                log.info("扫描件，先回占位提示：%s（%s 页）", path.name, pages)
                self.api.reply_text(
                    message_id,
                    f"这是扫描件，我正在本地识别文字（{pages} 页，约 {minutes} 分钟），"
                    "识别完直接回审查结果，不用重发。",
                )
            result = review_contract(path, self.settings)
        except ParseError as exc:
            log.info("文件读不出文字，已回提示：%s（文件 %s）", exc, path.name)
            self.api.reply_text(
                message_id,
                f"这份文件读不出来：{exc}\n\n"
                "扫描件（拍照/盖章后扫描的 PDF）是这样处理最快：\n"
                "1) 用 WPS 或「飞书文档」打开 PDF → 另存/转换为 Word 再发我；\n"
                "2) 或者把合同正文复制成文字直接发给我；\n"
                "3) 纯图片可以先在飞书里用「图片转文字」提成文本。",
            )
            return {"ok": False, "reason": "parse_failed"}
        except Exception:  # noqa: BLE001
            log.exception("合同解析/审查失败：%s", path)
            self.api.reply_text(message_id, "审查过程出错了，文件已保存，麻烦稍后重试或联系管理员。")
            return {"ok": False, "reason": "review_failed"}

        paths = write_report(result, self.settings.contract_out_path)
        findings = [f.to_dict() for f in result.findings]
        review_id = self.store.save(
            file_name=file_name or path.name,
            file_path=str(path),
            overall=result.overall,
            high=len(result.high),
            medium=len(result.medium),
            low=len(result.low),
            summary=result.summary,
            findings=findings,
            questions=result.questions,
            report_md=str(paths.get("markdown", "")),
            report_json=str(paths.get("json", "")),
            report_docx=str(paths.get("docx", "") or ""),
            chat_id=chat_id,
            operator_open_id=sender_open_id,
            message_id=message_id,
        )
        record = self.store.get(review_id) or {}
        if chat_type == "p2p":
            # 私聊=模板评审通道：先把「审批单上会出现的评论原文」摆出来
            comment_text = approval.build_comment_from_record(
                record, {"approval_name": self.settings.contract_preview_approval_name}
            )
            card = cards.comment_preview_card(record, comment_text)
        else:
            card = cards.review_card(record)
        try:
            self.api.reply_card(message_id, card)
        except FeishuError as exc:
            log.warning("回卡片失败，改发文本：%s", exc)
            self.api.reply_text(
                message_id,
                f"合同预审完成：综合风险 {result.overall}（高 {len(result.high)} 条）。"
                f"报告：{paths.get('docx') or paths.get('markdown')}",
            )

        if self.settings.contract_auto_task and self.settings.lawyer_open_ids:
            try:
                toast = self.create_task(review_id, operator_open_id="")
                log.info("自动建法务任务：%s", toast)
            except Exception:  # noqa: BLE001
                log.exception("自动建任务失败")

        log.info(
            "审查完成 %s（%s）：高 %d 中 %d 低 %d，review_id=%s",
            file_name,
            _human_size(size),
            len(result.high),
            len(result.medium),
            len(result.low),
            review_id,
        )
        return {
            "ok": True,
            "review_id": review_id,
            "overall": result.overall,
            "high": len(result.high),
            "report_docx": str(paths.get("docx", "") or ""),
        }

    def _in_scope(self, chat_id: str) -> bool:
        return bool(self.settings.contract_chat_ids and chat_id in self.settings.contract_chat_ids)

    # ---------- 事件：审批实例状态变更（用印申请） ----------
    def on_approval_event(self, event: Any) -> None:
        try:
            data = getattr(event, "event", event)
            if isinstance(data, dict):
                status = str(data.get("status", "") or "")
                approval_code = str(data.get("approval_code", "") or "")
                instance_code = str(data.get("instance_code", "") or "")
            else:
                status = str(getattr(data, "status", "") or "")
                approval_code = str(getattr(data, "approval_code", "") or "")
                instance_code = str(getattr(data, "instance_code", "") or "")
            log.info(
                "收到审批事件：status=%s approval_code=%s instance=%s",
                status, approval_code, instance_code,
            )
            if status != "PENDING":
                return
            allowed = self.settings.contract_approval_codes
            if allowed and approval_code not in allowed:
                log.info("审批定义 %s 不在接管范围，忽略", approval_code)
                return
            if not instance_code:
                return
            # 审查要调模型（十几秒），放到后台线程，避免阻塞长连接上的其它事件
            if getattr(self, "approval_async", True):
                import threading

                threading.Thread(
                    target=self._handle_approval,
                    args=(instance_code,),
                    name=f"approval-{instance_code[:8]}",
                    daemon=True,
                ).start()
            else:
                self._handle_approval(instance_code)
        except Exception:  # noqa: BLE001
            log.exception("处理审批事件失败")

    def _handle_approval(self, instance_code: str) -> None:
        """在后台线程里跑预审：审合同 → 写评论 → 通知审批人。"""
        try:
            result = approval.process_instance(
                self.api,
                self.settings,
                self.store,
                instance_code,
                post_comment=self.settings.contract_comment_enabled,
            )
            log.info(
                "审批预审完成：instance=%s ok=%s skipped=%s reason=%s comment_id=%s comment_error=%s",
                instance_code, result.get("ok"), result.get("skipped"), result.get("reason"),
                result.get("comment_id") or "-", result.get("comment_error") or "-",
            )
            if result.get("ok") and self.settings.contract_notify_approvers:
                self._notify_approvers(result)
        except Exception:  # noqa: BLE001
            log.exception("审批预审失败：instance=%s", instance_code)

    def _notify_approvers(self, result: dict[str, Any]) -> None:
        """把最严重的一份报告卡片私聊给当前待办的审批人。"""
        instance = result.get("instance") or {}
        targets: list[str] = []
        for task in instance.get("task_list") or []:
            if task.get("status") == "PENDING" and task.get("open_id"):
                targets.append(str(task["open_id"]))
        good = [r for r in (result.get("results") or []) if r.get("ok")]
        if not good:
            return
        worst = max(good, key=lambda r: r.get("high", 0) * 10 + r.get("medium", 0))
        record = self.store.get(str(worst["review_id"]))
        if not record:
            return
        card = cards.review_card(record)
        for open_id in dict.fromkeys(targets):
            try:
                self.api.send_card_to_open_id(open_id, card)
            except FeishuError as exc:
                log.warning("给审批人 %s 发卡片失败：%s", open_id, exc)

    # ---------- 事件：卡片按钮 ----------
    def on_card_action(self, event: Any) -> Any:
        try:
            body = event.event
            value = getattr(getattr(body, "action", None), "value", {}) or {}
            operator = getattr(getattr(body, "operator", None), "open_id", "") or ""
            toast = self.handle_action(value, operator_open_id=operator)
            log.info("卡片动作 %s -> %s", value, toast)
        except Exception:  # noqa: BLE001
            log.exception("处理卡片回调失败")
            toast = "操作失败，请稍后重试"
        try:
            from lark_oapi.api.im.v1 import P2CardActionTriggerResponse

            return P2CardActionTriggerResponse({"toast": {"type": "success", "content": toast}})
        except Exception:  # noqa: BLE001
            return {"toast": {"type": "success", "content": toast}}

    def handle_action(self, value: dict[str, Any], *, operator_open_id: str = "") -> str:
        """卡片按钮已停用（2026-11-14 用户要求去掉）。

        历史卡片上残留的按钮点下去只回一句提示，不做任何事。
        """
        action = str(value.get("action", ""))
        if action:
            log.info("收到已停用的卡片动作：%s", action)
        return "卡片按钮已停用，结论请看审批单评论"

    def create_task(self, review_id: str, *, operator_open_id: str = "") -> str:
        record = self.store.get(review_id)
        if not record:
            return "这条审查记录不存在或已过期"
        summary = f"审合同：{record['file_name']}（风险{record['overall']}）"
        top = [f for f in (record.get("findings") or []) if f.get("level") == "high"][:5]
        lines = [
            f"综合风险：{record['overall']}（高 {record['high']} ｜ 中 {record['medium']} ｜ 低 {record['low']}）",
            f"合同金额：{(record.get('summary') or {}).get('amount') or '—'}",
            f"对方主体：{(record.get('summary') or {}).get('counterparty') or '—'}",
            "",
            "重点风险：",
        ]
        lines.extend(f"- {item.get('title')}" for item in top)
        if record.get("report_md"):
            lines.append(f"\n完整报告：{record['report_md']}")
        due_ts = (
            datetime.now()
            .replace(hour=18, minute=0, second=0, microsecond=0)
            + timedelta(days=max(self.settings.contract_task_due_days, 0))
        ).timestamp()
        lawyer = self.settings.lawyer_open_ids[0] if self.settings.lawyer_open_ids else ""
        try:
            result = self.auth.create_task(
                summary=summary,
                description="\n".join(lines),
                due_ts=due_ts,
                assignee_open_id=lawyer,
            )
        except AuthError as exc:
            return f"建任务失败：{exc}"
        task = result.get("task") or {}
        url = str(task.get("url", "") or "")
        self.store.update(review_id, status="tasked", task_url=url)
        who = "法务" if lawyer else "你"
        return f"已给{who}建好任务：{summary}"


# ---------- 启动 ----------
def run(settings: Settings | None = None) -> None:
    settings = settings or Settings.load()
    if not settings.app_id or not settings.app_secret:
        raise SystemExit("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，请先填写 .env")

    try:
        import lark_oapi as lark
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("启动长连接需要 lark-oapi，请先运行：python -m src.cli doctor 查看依赖") from exc

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = ContractBot(settings)
    try:
        info = bot.api.bot_info()
        log.info("机器人已就绪：%s", info.get("app_name") or info.get("name") or "contract-bot")
    except FeishuError as exc:
        log.warning(
            "读取机器人信息失败（可能还没开启「机器人」能力）：%s\n"
            "请到开放平台 → 添加应用能力 → 机器人，并发布版本。",
            exc,
        )

    dispatcher = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(
        bot.on_message
    ).register_p2_card_action_trigger(bot.on_card_action).register_p2_approval_instance_status_changed_v4(
        bot.on_approval_event
    ).register_p1_customized_event(
        # 老版审批事件「审批实例状态变更 approval_instance」，控制台只提供这一版
        "approval_instance",
        bot.on_approval_event,
    ).build()

    ws_client = lark.ws.Client(
        settings.app_id,
        settings.app_secret,
        event_handler=dispatcher,
        log_level=lark.LogLevel.INFO,
    )
    log.info("合同审查机器人已启动，等待飞书事件（Ctrl+C 退出）")
    try:
        ws_client.start()
    except KeyboardInterrupt:
        pass


__all__ = ["ContractBot", "run"]
