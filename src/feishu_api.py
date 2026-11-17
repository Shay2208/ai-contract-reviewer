"""飞书开放平台 REST 客户端（只用标准库 urllib，零第三方依赖）。

覆盖本项目需要的接口：tenant_access_token、发消息/发卡片、读群信息、
多维表格建表/建记录/更新记录。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def _guess_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".pdf": "pdf",
        ".doc": "doc",
        ".docx": "doc",
        ".xls": "xls",
        ".xlsx": "xls",
        ".ppt": "ppt",
        ".pptx": "ppt",
        ".mp4": "mp4",
        ".opus": "opus",
    }.get(suffix, "stream")


class FeishuError(RuntimeError):
    pass


class FeishuAPI:
    BASE = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id: str, app_secret: str, timeout: int = 20) -> None:
        if not app_id or not app_secret:
            raise FeishuError("缺少 app_id / app_secret")
        self.app_id = app_id
        self.app_secret = app_secret
        self.timeout = timeout
        self._token: str = ""
        self._token_expire: float = 0.0
        self._lock = threading.Lock()

    # ---------- 基础 ----------
    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        auth: bool = True,
        unwrap: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = f"Bearer {self.tenant_access_token()}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise FeishuError(f"飞书 HTTP {exc.code}: {detail}") from exc
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise FeishuError(f"飞书返回非 JSON: {body[:200]}") from exc
        code = parsed.get("code")
        if code not in (0, None):
            raise FeishuError(f"飞书 API 错误 {code}: {parsed.get('msg')}")
        return parsed if not unwrap else (parsed.get("data", {}) or {})

    def tenant_access_token(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.time() < self._token_expire - 60:
                return self._token
            data = self._request(
                "POST",
                "/auth/v3/tenant_access_token/internal",
                payload={"app_id": self.app_id, "app_secret": self.app_secret},
                auth=False,
                unwrap=False,
            )
            self._token = data.get("tenant_access_token", "")
            expire = int(data.get("expire", 7200))
            self._token_expire = time.time() + expire
            if not self._token:
                raise FeishuError("获取 tenant_access_token 失败")
            return self._token

    # ---------- 群 / 消息 ----------
    def get_chat(self, chat_id: str) -> dict[str, Any]:
        """读群信息，返回包含 name 的字典（长连接事件里不带群名，需要补一次）。"""
        return self._request("GET", f"/im/v1/chats/{chat_id}")

    def get_user(self, open_id: str) -> dict[str, Any]:
        """读用户信息（可选，用于把 open_id 换成姓名，需要通讯录只读权限）。"""
        data = self._request(
            "GET", f"/contact/v3/users/{open_id}", params={"user_id_type": "open_id"}
        )
        return data.get("user", {}) or {}

    def bot_info(self) -> dict[str, Any]:
        """机器人基本信息（需要应用开启「机器人」能力）。"""
        return self._request("GET", "/bot/v3/info")

    def list_chats(self, page_size: int = 50) -> list[dict[str, Any]]:
        """机器人所在的群列表（需要 im:chat:readonly）。"""
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            data = self._request("GET", "/im/v1/chats", params=params)
            items.extend(data.get("items", []) or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
        return items

    def list_chat_members(self, chat_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        """群成员列表（需要 im:chat.members:readonly），用于确认客服 open_id。"""
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"member_id_type": "open_id", "page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            data = self._request("GET", f"/im/v1/chats/{chat_id}/members", params=params)
            items.extend(data.get("items", []) or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
        return items

    def list_messages(
        self,
        chat_id: str,
        start_ts: int,
        end_ts: int | None = None,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """拉取群历史消息（需要敏感权限「获取群组中所有消息」im:message.group_msg）。

        用于首次回灌历史数据：长连接事件只从启动那一刻开始收消息，
        试点第一天需要把过去几天的客服对话补进来。
        """
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {
                "container_id_type": "chat",
                "container_id": chat_id,
                "start_time": int(start_ts),
                "sort_type": "ByCreateTimeAsc",
                "page_size": page_size,
            }
            if end_ts:
                params["end_time"] = int(end_ts)
            if page_token:
                params["page_token"] = page_token
            data = self._request("GET", "/im/v1/messages", params=params)
            items.extend(data.get("items", []) or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
        return items

    def send_card(self, chat_id: str, card: dict[str, Any]) -> str:
        return self._send_message("chat_id", chat_id, "interactive", json.dumps(card, ensure_ascii=False))

    def send_card_to_open_id(self, open_id: str, card: dict[str, Any]) -> str:
        return self._send_message("open_id", open_id, "interactive", json.dumps(card, ensure_ascii=False))

    def send_text(self, chat_id: str, text: str) -> str:
        return self._send_message("chat_id", chat_id, "text", json.dumps({"text": text}, ensure_ascii=False))

    def reply_card(self, message_id: str, card: dict[str, Any]) -> str:
        data = self._request(
            "POST",
            f"/im/v1/messages/{message_id}/reply",
            payload={"msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
        )
        return str(data.get("message_id", ""))

    def _send_message(self, id_type: str, receive_id: str, msg_type: str, content: str) -> str:
        data = self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": id_type},
            payload={"receive_id": receive_id, "msg_type": msg_type, "content": content},
        )
        return str(data.get("message_id", ""))

    def reply_text(self, message_id: str, text: str) -> str:
        data = self._request(
            "POST",
            f"/im/v1/messages/{message_id}/reply",
            payload={"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        )
        return str(data.get("message_id", ""))

    # ---------- 文件 ----------
    def download_file(
        self,
        message_id: str,
        file_key: str,
        out_path: str | Path,
        resource_type: str = "file",
    ) -> Path:
        """下载消息里的文件/图片（需要 im:resource 权限）。"""
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"{self.BASE}/im/v1/messages/{message_id}/resources/{file_key}"
        params = urllib.parse.urlencode({"type": resource_type})
        headers = {"Authorization": f"Bearer {self.tenant_access_token()}"}
        request = urllib.request.Request(f"{url}?{params}", headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=max(self.timeout, 60)) as response:
                path.write_bytes(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise FeishuError(f"下载文件失败 HTTP {exc.code}: {detail}") from exc
        return path

    def upload_file(self, path: str | Path, file_type: str = "", file_name: str = "") -> str:
        """上传文件（用于把 Word 报告发回给用户），返回 file_key。"""
        path = Path(path)
        if not path.exists():
            raise FeishuError(f"文件不存在：{path}")
        ftype = file_type or _guess_file_type(path)
        name = file_name or path.name
        boundary = "----dsh" + uuid.uuid4().hex
        chunks: list[bytes] = []

        def field(key: str, value: str) -> None:
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
            )

        field("file_type", ftype)
        field("file_name", name)
        chunks.append(
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
        )
        chunks.append(path.read_bytes())
        chunks.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        headers = {
            "Authorization": f"Bearer {self.tenant_access_token()}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        request = urllib.request.Request(
            f"{self.BASE}/im/v1/files", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=max(self.timeout, 60)) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise FeishuError(f"上传文件失败 HTTP {exc.code}: {detail}") from exc
        code = parsed.get("code")
        if code not in (0, None):
            raise FeishuError(f"飞书 API 错误 {code}: {parsed.get('msg')}")
        file_key = str((parsed.get("data") or {}).get("file_key", ""))
        if not file_key:
            raise FeishuError("上传文件未返回 file_key")
        return file_key

    def send_file(self, chat_id: str, file_key: str) -> str:
        return self._send_message("chat_id", chat_id, "file", json.dumps({"file_key": file_key}))

    def send_file_to_open_id(self, open_id: str, file_key: str) -> str:
        return self._send_message("open_id", open_id, "file", json.dumps({"file_key": file_key}))

    def reply_file(self, message_id: str, file_key: str) -> str:
        data = self._request(
            "POST",
            f"/im/v1/messages/{message_id}/reply",
            payload={"msg_type": "file", "content": json.dumps({"file_key": file_key})},
        )
        return str(data.get("message_id", ""))

    # ---------- 审批（原生审批 v4） ----------
    def get_approval(self, approval_code: str) -> dict[str, Any]:
        """读取审批定义（表单控件、节点）。"""
        return self._request("GET", f"/approval/v4/approvals/{approval_code}")

    def get_instance(self, instance_code: str, locale: str = "zh-CN") -> dict[str, Any]:
        """读取审批实例详情（form 是 JSON 字符串，含各控件值）。"""
        return self._request(
            "GET",
            f"/approval/v4/instances/{instance_code}",
            params={"locale": locale, "user_id_type": "open_id"},
        )

    def create_comment(
        self,
        instance_code: str,
        text: str,
        at_open_ids: list[str] | None = None,
        user_id: str = "",
        user_id_type: str = "open_id",
    ) -> str:
        """在审批实例下创建评论，返回评论 ID。user_id 为评论人（应用身份调用时必填）。"""
        payload: dict[str, Any] = {"content": json.dumps({"text": text}, ensure_ascii=False)}
        if at_open_ids:
            payload["at_info_list"] = [
                {"user_id": oid, "name": "", "offset": str(text.find("@"))} for oid in at_open_ids
            ]
        params: dict[str, Any] = {"user_id_type": user_id_type}
        if user_id:
            params["user_id"] = user_id
        data = self._request(
            "POST",
            f"/approval/v4/instances/{instance_code}/comments",
            params=params,
            payload=payload,
        )
        return str(data.get("comment_id", ""))

    def subscribe_instance_event(self, approval_code: str) -> dict[str, Any]:
        """订阅某个审批定义的实例事件（应用身份 tenant_access_token 即可）。

        实测：POST /approval/v4/approvals/{approval_code}/subscribe
        返回 {"code":0,"data":{},"msg":"success"}；重复订阅报 1390007 subscription existed。
        注意 /approval/v4/instances/subscription 是另一个接口（要 subscription_type），不用于此处。
        """
        return self._request(
            "POST", f"/approval/v4/approvals/{approval_code}/subscribe", payload={}
        )

    @staticmethod
    def download_url(url: str, out_path: str | Path, timeout: int = 60) -> Path:
        """下载审批附件（表单 attachmentV2 的 value 是 12 小时有效的签名 URL）。"""
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=timeout) as response:
            path.write_bytes(response.read())
        return path

    # ---------- 多维表格 ----------
    def bitable_create_app(self, name: str, folder_token: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if folder_token:
            payload["folder_token"] = folder_token
        return self._request("POST", "/bitable/v1/apps", payload=payload)

    def bitable_create_table(
        self, app_token: str, name: str, fields: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            payload={"table": {"name": name, "default_view_name": "全部工单", "fields": fields}},
        )

    def bitable_list_tables(self, app_token: str) -> list[dict[str, Any]]:
        data = self._request("GET", f"/bitable/v1/apps/{app_token}/tables")
        return data.get("items", []) or []

    def bitable_list_records(
        self, app_token: str, table_id: str, page_size: int = 100
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            data = self._request(
                "GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records", params=params
            )
            items.extend(data.get("items", []) or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
        return items

    def bitable_create_record(
        self, app_token: str, table_id: str, fields: dict[str, Any]
    ) -> str:
        data = self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            payload={"fields": fields},
        )
        return str((data.get("record") or {}).get("record_id", ""))

    def bitable_update_record(
        self, app_token: str, table_id: str, record_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            payload={"fields": fields},
        )

    def bitable_delete_record(self, app_token: str, table_id: str, record_id: str) -> None:
        self._request(
            "DELETE", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        )


__all__ = ["FeishuAPI", "FeishuError"]
