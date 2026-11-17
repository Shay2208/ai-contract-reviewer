"""审批相关子命令：approval-review、approval-subscribe。"""

from __future__ import annotations

from ..config import Settings


def cmd_approval_review(args) -> int:
    """对一个审批实例跑合同预审（可选自动写评论）。"""
    from ..contract import approval as approval_mod
    from ..contract.store import ContractStore
    from ..feishu_api import FeishuAPI, FeishuError

    settings = Settings.load()
    api = FeishuAPI(settings.app_id, settings.app_secret)
    store = ContractStore(settings.db_file)
    code = approval_mod.extract_instance_code(args.instance)

    print("=" * 72)
    print("审批实例 · 合同预审")
    print("=" * 72)
    print(f"实例：{code}")
    try:
        result = approval_mod.process_instance(
            api, settings, store, code,
            post_comment=not args.no_comment, force=args.force,
        )
    except FeishuError as exc:
        print(f"[X] 读取审批实例失败：{exc}")
        return 1

    inst = result.get("instance") or {}
    print(f"审批：{inst.get('approval_name') or '(未知)'}（{inst.get('approval_code') or ''}）")
    print(f"状态：{inst.get('status') or ''}　编号：{inst.get('serial_number') or ''}")
    print(f"判定：{result.get('reason') or ''}")
    for item in result.get("results") or []:
        if item.get("ok"):
            print(f"  [OK] {item['file']} → 综合风险 {item['overall']}（高 {item['high']} / 中 {item['medium']}）")
            for key, path in (item.get("reports") or {}).items():
                print(f"       {key}: {path}")
        else:
            print(f"  [X] {item.get('file')}：{item.get('error')}")
    if result.get("comment_id"):
        print(f"\n已写入审批评论：{result['comment_id']}")
    elif result.get("comment"):
        print("\n评论内容（未写入审批）：\n" + result["comment"])
    return 0 if result.get("ok") else 1


def cmd_approval_subscribe(args) -> int:
    """订阅审批实例状态变更事件（应用身份即可）。"""
    from ..contract import approval as approval_mod

    settings = Settings.load()
    codes = [args.approval_code] if args.approval_code else settings.contract_approval_codes
    if not codes:
        print("请给出 approval_code，或先在 .env 配置 CONTRACT_APPROVAL_CODES")
        return 1
    ok = True
    for code in codes:
        try:
            resp = approval_mod.subscribe_instance_event(settings, code)
            print(f"[OK] 已订阅 {code}：{resp}")
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "1390007" in text or "subscription existed" in text:
                print(f"[OK] {code} 之前已订阅（{text}）")
            else:
                ok = False
                print(f"[X] 订阅 {code} 失败：{text}")
    return 0 if ok else 1
