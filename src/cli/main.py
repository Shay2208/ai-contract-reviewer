"""命令行顶层：argparse 解析器、共用小工具与子命令注册。"""

from __future__ import annotations

import argparse

from .commands_approval import cmd_approval_review, cmd_approval_subscribe
from .commands_auth import cmd_login, cmd_logout, cmd_task_add, cmd_task_list, cmd_whoami
from .commands_bot import cmd_contract_bot, cmd_contract_demo
from .commands_misc import cmd_doctor
from .commands_review import cmd_contracts, cmd_preview_comment, cmd_review_contract


def _sep(title: str = "") -> None:
    line = "=" * 72
    print(f"\n{line}")
    if title:
        print(title)
        print(line)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="service-closure-bot",
        description="示例企业 · 客服服务闭环机器人（演示版）",
    )
    sub = parser.add_subparsers(dest="command")

    doctor = sub.add_parser("doctor", help="真实接入自检：配置、凭证、机器人能力、审批接管、用户授权")
    doctor.set_defaults(func=cmd_doctor)

    login = sub.add_parser("login", help="用你的飞书账号授权（OAuth，一次；之后以你的身份建任务/写文档）")
    login.add_argument("--port", type=int, default=8787, help="本地回调端口（默认 8787）")
    login.add_argument("--manual", action="store_true", help="无法自动接收回调时，手动粘贴浏览器跳转后的地址")
    login.add_argument("--timeout", type=int, default=900, help="等待授权回调的秒数（默认 900）")
    login.set_defaults(func=cmd_login)

    whoami = sub.add_parser("whoami", help="查看当前已授权的飞书账号")
    whoami.set_defaults(func=cmd_whoami)

    task_add = sub.add_parser("task-add", help="以你的身份在飞书新建一项任务")
    task_add.add_argument("summary", nargs="+", help="任务内容")
    task_add.add_argument("--due", default="", help="截止时间：今天/明天18:00/本周五/3天内 等")
    task_add.add_argument("--desc", default="", help="任务描述")
    task_add.set_defaults(func=cmd_task_add)

    task_list = sub.add_parser("task-list", help="列出你飞书里的未完成任务")
    task_list.add_argument("--limit", type=int, default=50, help="最多列出多少条（默认 50）")
    task_list.set_defaults(func=cmd_task_list)

    logout = sub.add_parser("logout", help="删除本地保存的飞书用户 token")
    logout.set_defaults(func=cmd_logout)

    review = sub.add_parser("review-contract", help="合同 AI 预审：清单体检 + 风险清单 + 一页纸报告")
    review.add_argument("file", help="合同文件路径（.docx / .pdf / .txt / .md）")
    review.add_argument("--out", default="", help="报告输出目录（默认 out/contracts）")
    review.add_argument("--rules", action="store_true", help="强制仅规则模式，不调用大模型")
    review.set_defaults(func=cmd_review_contract)

    contract_bot = sub.add_parser("contract-bot", help="启动合同审查机器人（飞书长连接，发文件即审）")
    contract_bot.set_defaults(func=cmd_contract_bot)

    contract_demo = sub.add_parser("contract-demo", help="本地模拟「发合同 → 回卡片」全流程，不连飞书")
    contract_demo.add_argument("file", nargs="?", default="", help="合同文件（默认 data/sample_contract.txt）")
    contract_demo.add_argument("--rules", action="store_true", help="强制仅规则模式")
    contract_demo.set_defaults(func=cmd_contract_demo)

    contracts = sub.add_parser("contracts", help="列出最近的合同审查记录")
    contracts.add_argument("--limit", type=int, default=10, help="最多列出多少条（默认 10）")
    contracts.set_defaults(func=cmd_contracts)

    approval_review = sub.add_parser("approval-review", help="对一个审批实例做合同预审并回写评论")
    approval_review.add_argument("instance", help="审批实例 code 或带 instance_code 的链接")
    approval_review.add_argument("--no-comment", action="store_true", help="只审查，不写审批评论")
    approval_review.add_argument("--force", action="store_true", help="表单未勾选「合同」也强制审查")
    approval_review.set_defaults(func=cmd_approval_review)

    approval_sub = sub.add_parser("approval-subscribe", help="订阅审批实例状态变更事件（需用户身份）")
    approval_sub.add_argument("approval_code", nargs="?", default="", help="审批定义 Code（默认取 CONTRACT_APPROVAL_CODES）")
    approval_sub.set_defaults(func=cmd_approval_subscribe)

    preview = sub.add_parser("preview-comment", help="只看审批评论样稿（不写审批，打印到屏幕）")
    preview.add_argument("file", help="合同文件（docx/pdf/txt）")
    preview.add_argument("--json", action="store_true", help="同时输出结构化 JSON")
    preview.add_argument("--approval-name", default="", help="冒充的审批名（默认 CONTRACT_PREVIEW_APPROVAL_NAME）")
    preview.set_defaults(func=cmd_preview_comment)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)
