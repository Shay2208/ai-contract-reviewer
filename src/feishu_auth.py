"""飞书「用户身份」授权（OAuth 2.0）与以用户身份执行操作。

飞书不支持账号密码登录，也不该保存密码。正确做法：
  1) 应用后台配置重定向 URL（默认 http://localhost:8787/callback）
  2) 用户在浏览器点一次「授权」，拿到 user_access_token（2 小时）+ refresh_token（30 天）
  3) token 落盘 data/user_tokens.json（已 gitignore），过期自动刷新
  4) 之后就能以「用户本人」身份调接口，例如新建飞书任务、写文档、发日程

本模块不依赖 lark-oapi，只用标准库 urllib。
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .config import ROOT, Settings

AUTH_URL = "https://open.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
TASK_V2_URL = "https://open.feishu.cn/open-apis/task/v2/tasks"
TASK_V1_URL = "https://open.feishu.cn/open-apis/task/v1/tasks"

DEFAULT_REDIRECT_URI = "http://localhost:8787/callback"
DEFAULT_SCOPES = "task:task:write task:task:read"


class AuthError(RuntimeError):
    """授权或调用失败（带飞书原始错误信息，方便排查）。"""


def _http_json(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    token: str = "",
    timeout: int = 20,
) -> dict:
    data = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise AuthError(f"飞书接口 HTTP {exc.code}：{raw[:500]}") from None
    except urllib.error.URLError as exc:
        raise AuthError(f"连不上飞书（检查网络/代理）：{exc.reason}") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise AuthError(f"飞书返回的不是 JSON：{raw[:300]}") from None


class UserAuth:
    """管理用户 token：登录、刷新、以用户身份调接口。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.redirect_uri = settings.oauth_redirect_uri or DEFAULT_REDIRECT_URI
        self.scopes = settings.oauth_scopes or DEFAULT_SCOPES

    # ---------- token 存储 ----------
    @property
    def token_path(self) -> Path:
        return ROOT / "data" / "user_tokens.json"

    def _load(self) -> dict:
        if not self.token_path.exists():
            return {}
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    def _save(self, tokens: dict[str, Any]) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")

    def logout(self) -> None:
        if self.token_path.exists():
            self.token_path.unlink()

    def logged_in(self) -> bool:
        return bool(self._load().get("access_token") or self._load().get("refresh_token"))

    # ---------- 授权流程 ----------
    def authorize_url(self, state: str) -> str:
        query = urllib.parse.urlencode(
            {
                "app_id": self.settings.app_id,
                "redirect_uri": self.redirect_uri,
                "scope": self.scopes,
                "state": state,
            }
        )
        return f"{AUTH_URL}?{query}"

    def login(self, port: int = 8787, manual: bool = False, timeout: int = 300) -> dict:
        """打印/打开授权页，等待回调，换 token。manual=True 时改为粘贴回调地址。"""
        if not self.settings.app_id or not self.settings.app_secret:
            raise AuthError("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，先填 .env")
        state = secrets.token_urlsafe(12)
        url = self.authorize_url(state)

        print("=" * 72)
        print("飞书账号授权（只需一次；不是账号密码，是飞书官方的授权页）")
        print("=" * 72)
        print("\n第 1 步：确认应用后台已配置「安全设置 → 重定向 URL」：")
        print(f"    {self.redirect_uri}")
        print("\n第 2 步：在浏览器打开下面的链接，用你的飞书账号登录并点「同意授权」：\n")
        print(url)
        print()

        code = ""
        if manual:
            pasted = input("第 3 步：授权后浏览器会跳到 callback 地址，把地址栏完整 URL 粘贴到这里：\n> ").strip()
            parsed = urllib.parse.urlparse(pasted)
            query = urllib.parse.parse_qs(parsed.query)
            code = (query.get("code") or [""])[0]
            got_state = (query.get("state") or [""])[0]
            if got_state and got_state != state:
                raise AuthError("state 不匹配，可能复制错了地址，请重新登录")
            if not code:
                raise AuthError("粘贴的地址里没有 code 参数")
        else:
            webbrowser.open(url)
            print(f"第 3 步：已尝试打开浏览器；等待回调 {self.redirect_uri}（{timeout} 秒超时）…")
            result = self._wait_for_callback(port, state, timeout)
            code = result.get("code", "")
            if result.get("error"):
                raise AuthError(f"授权失败：{result['error']}")

        if not code:
            raise AuthError("没有拿到授权 code")
        tokens = self._exchange_code(code)
        tokens["saved_at"] = int(time.time())
        self._save(tokens)
        info = self.user_info(tokens["access_token"])
        tokens.update({"name": info.get("name", ""), "open_id": info.get("open_id", "")})
        self._save(tokens)
        return tokens

    def _wait_for_callback(self, port: int, state: str, timeout: int) -> dict:
        captured: dict[str, str] = {}
        done = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path not in {"/callback", "/"}:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = urllib.parse.parse_qs(parsed.query)
                code = (query.get("code") or [""])[0]
                got_state = (query.get("state") or [""])[0]
                error = (query.get("error_description") or query.get("error") or [""])[0]
                if error:
                    captured["error"] = error
                elif got_state != state:
                    captured["error"] = "state 不匹配"
                elif not code:
                    captured["error"] = "回调里没有 code"
                else:
                    captured["code"] = code
                body = (
                    "<html><meta charset='utf-8'><body style='font-family:sans-serif;padding:40px'>"
                    "<h2>授权完成</h2><p>可以关闭这个页面，回到命令行继续。</p></body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                done.set()

            def log_message(self, *args):  # 静默
                return

        try:
            server = HTTPServer(("127.0.0.1", port), Handler)
        except OSError as exc:
            raise AuthError(
                f"本地端口 {port} 被占用或无法监听：{exc}；可换端口（--port）或加 --manual 手动粘贴"
            ) from None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            if not done.wait(timeout):
                raise AuthError(f"等待回调超时（{timeout} 秒）；可重新运行并加 --manual 手动粘贴")
        finally:
            server.shutdown()
            server.server_close()
        return captured

    def _exchange_code(self, code: str) -> dict:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.settings.app_id,
            "client_secret": self.settings.app_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
        }
        result = _http_json(TOKEN_URL, method="POST", payload=payload)
        tokens = self._tokens_from_response(result)
        tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in") or 7200)
        return tokens

    def _tokens_from_response(self, result: dict) -> dict:
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        if not data.get("access_token"):
            raise AuthError(f"换取 token 失败：{json.dumps(result, ensure_ascii=False)[:400]}")
        return {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "scope": data.get("scope", ""),
            "expires_in": data.get("expires_in", 7200),
            "refresh_token_expires_in": data.get("refresh_token_expires_in", 0),
        }

    def _refresh(self, refresh_token: str) -> dict:
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.settings.app_id,
            "client_secret": self.settings.app_secret,
            "refresh_token": refresh_token,
        }
        result = _http_json(TOKEN_URL, method="POST", payload=payload)
        tokens = self._tokens_from_response(result)
        tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in") or 7200)
        return tokens

    def access_token(self) -> str:
        tokens = self._load()
        if not tokens:
            raise AuthError("还没有登录飞书用户账号，请先运行：python -m src.cli login")
        if tokens.get("access_token") and tokens.get("expires_at", 0) > time.time() + 120:
            return tokens["access_token"]
        if not tokens.get("refresh_token"):
            raise AuthError("授权已过期且没有 refresh_token，请重新运行：python -m src.cli login")
        refreshed = self._refresh(tokens["refresh_token"])
        refreshed.update({"name": tokens.get("name", ""), "open_id": tokens.get("open_id", "")})
        refreshed["saved_at"] = int(time.time())
        self._save(refreshed)
        return refreshed["access_token"]

    # ---------- 用户身份接口 ----------
    def user_info(self, token: str = "") -> dict:
        token = token or self.access_token()
        result = _http_json(USER_INFO_URL, token=token)
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        if result.get("code") not in (0, None):
            raise AuthError(f"读取用户信息失败：{result.get('msg')}（code={result.get('code')}）")
        return data

    def list_tasks(self, page_size: int = 50) -> list[dict]:
        """以用户身份列出（未完成）任务。优先 task v2，失败回退 v1。"""
        token = self.access_token()
        errors: list[str] = []
        for url in (TASK_V2_URL, TASK_V1_URL):
            query = urllib.parse.urlencode({"page_size": page_size, "completed": "false"})
            try:
                result = _http_json(f"{url}?{query}", token=token)
            except AuthError as exc:
                errors.append(f"{url}: {exc}")
                continue
            if result.get("code") not in (0, None):
                errors.append(f"{url}: code={result.get('code')} msg={result.get('msg')}")
                continue
            data = result.get("data") if isinstance(result.get("data"), dict) else result
            return data.get("items") or []
        raise AuthError("读取任务列表失败：\n  " + "\n  ".join(errors))

    def _current_open_id(self) -> str:
        tokens = self._load()
        open_id = tokens.get("open_id") or ""
        if not open_id:
            open_id = self.user_info().get("open_id", "")
            tokens["open_id"] = open_id
            self._save(tokens)
        return open_id

    def create_task(
        self,
        summary: str,
        description: str = "",
        due_ts: float | None = None,
        assignee_open_id: str = "",
    ) -> dict:
        """以用户身份新建飞书任务（默认把你自己设为负责人，确保出现在「我的任务」）。"""
        token = self.access_token()
        due = None
        if due_ts:
            due = {"timestamp": str(int(due_ts * 1000)), "is_all_day": False}
        payload: dict[str, Any] = {"summary": summary}
        if description:
            payload["description"] = description
        if due:
            payload["due"] = due
        open_id = assignee_open_id or self._current_open_id()
        if open_id:
            payload["members"] = [{"id": open_id, "type": "user", "role": "assignee"}]
        errors: list[str] = []
        for url in (TASK_V2_URL, TASK_V1_URL):
            body = dict(payload)
            if url == TASK_V1_URL:
                body["origin"] = {"platform_i18n_name": "合同预审助手"}
            try:
                result = _http_json(url, method="POST", payload=body, token=token)
            except AuthError as exc:
                errors.append(f"{url}: {exc}")
                continue
            code = result.get("code")
            if code not in (0, None):
                errors.append(f"{url}: code={code} msg={result.get('msg')}")
                continue
            data = result.get("data") if isinstance(result.get("data"), dict) else result
            task = data.get("task") if isinstance(data.get("task"), dict) else data
            return {"task": task, "via": "v2" if url == TASK_V2_URL else "v1"}
        raise AuthError("新建任务失败：\n  " + "\n  ".join(errors))
