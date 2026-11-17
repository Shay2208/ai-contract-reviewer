"""账号与任务子命令：login、logout、whoami、task-add、task-list。"""

from __future__ import annotations

import json
import time
from datetime import datetime

from ..config import Settings


def cmd_login(args) -> int:
    """用你的飞书账号做一次 OAuth 授权（不是账号密码）。"""
    from ..feishu_auth import AuthError, UserAuth

    settings = Settings.load()
    auth = UserAuth(settings)
    try:
        tokens = auth.login(port=args.port, manual=args.manual, timeout=args.timeout)
    except AuthError as exc:
        print(f"[X] 登录失败：{exc}")
        print(f"    常见原因：应用后台「安全设置 → 重定向 URL」还没加 {auth.redirect_uri}")
        return 1
    print(f"[OK] 已登录飞书：{tokens.get('name') or '(未返回姓名)'}")
    print(f"     open_id={tokens.get('open_id') or '-'}")
    print(f"     token 已保存到：{auth.token_path}（2 小时有效，到期自动刷新）")
    print("\n现在可以直接跟我说：帮我新建一项任务：……")
    return 0


def cmd_whoami(args) -> int:
    from ..feishu_auth import AuthError, UserAuth

    auth = UserAuth(Settings.load())
    try:
        info = auth.user_info()
    except AuthError as exc:
        print(f"[X] {exc}")
        return 1
    keys = ("name", "en_name", "open_id", "union_id", "user_id", "tenant_key")
    print(json.dumps({k: info.get(k) for k in keys if info.get(k)}, ensure_ascii=False, indent=2))
    tokens = auth._load()
    if tokens.get("expires_at"):
        print(f"\ntoken 到期：{datetime.fromtimestamp(tokens['expires_at']).strftime('%Y-%m-%d %H:%M:%S')}（过期后自动刷新）")
    return 0


def cmd_task_add(args) -> int:
    """以你的身份在飞书「任务」里新建一条任务。"""
    from ..dates import parse_due
    from ..feishu_auth import AuthError, UserAuth

    summary = " ".join(args.summary).strip()
    if not summary:
        print("[X] 任务内容不能为空")
        return 1
    due_ts = None
    if args.due:
        due_ts = parse_due(args.due, time.time())
        if not due_ts:
            print(f"[X] 没看懂截止时间：{args.due}（示例：明天18:00 / 本周五 / 3天内）")
            return 1
    auth = UserAuth(Settings.load())
    try:
        result = auth.create_task(summary, args.desc, due_ts)
    except AuthError as exc:
        print(f"[X] 新建任务失败：{exc}")
        print("    排查：① 是否已运行 python -m src.cli login；② 应用是否已发布；③ 是否勾选 task:task:write 权限")
        return 1
    task = result.get("task") or {}
    print(f"[OK] 已在飞书新建任务（{result.get('via')}）：{summary}")
    print(f"     task_id：{task.get('task_id') or task.get('id') or '-'}")
    if task.get("guid"):
        print(f"     guid：{task['guid']}")
    if task.get("url"):
        print(f"     链接：{task['url']}")
    if due_ts:
        print(f"     截止时间：{datetime.fromtimestamp(due_ts).strftime('%Y-%m-%d %H:%M')}")
    print("     打开飞书「任务」即可看到。")
    return 0


def cmd_task_list(args) -> int:
    """列出你飞书里的未完成任务。"""
    from ..feishu_auth import AuthError, UserAuth

    auth = UserAuth(Settings.load())
    try:
        tasks = auth.list_tasks(page_size=args.limit)
    except AuthError as exc:
        print(f"[X] {exc}")
        return 1
    if not tasks:
        print("（没有未完成任务）")
        return 0
    print(f"未完成任务 {len(tasks)} 条：")
    for task in tasks:
        due = (task.get("due") or {}).get("timestamp") or ""
        due_text = "-"
        if due:
            try:
                due_text = datetime.fromtimestamp(int(due) / 1000).strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError):
                due_text = str(due)
        tid = task.get("guid") or task.get("id") or "-"
        print(f"  - {task.get('summary') or '(无标题)'}　截止：{due_text}　id={tid}")
    return 0


def cmd_logout(args) -> int:
    from ..feishu_auth import UserAuth

    auth = UserAuth(Settings.load())
    auth.logout()
    print(f"已删除本地 token：{auth.token_path}")
    return 0
