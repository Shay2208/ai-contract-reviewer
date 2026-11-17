"""合同审查子命令：review-contract、preview-comment、contracts。"""

from __future__ import annotations

import time
from pathlib import Path

from ..config import ROOT, Settings


def cmd_review_contract(args) -> int:
    """合同 AI 预审：清单体检 + 语义审查 + 一页纸报告。"""
    from ..contract.parser import ParseError
    from ..contract.render import write_report
    from ..contract.reviewer import review_contract

    settings = Settings.load()
    path = Path(args.file)
    if not path.is_absolute() and not path.exists():
        alt = ROOT / path
        if alt.exists():
            path = alt

    print("=" * 72)
    print("合同 AI 预审 review-contract")
    print("=" * 72)
    print(f"文件：{path}")
    if args.rules:
        mode_text = "仅规则清单（--rules）"
    elif settings.use_llm:
        mode_text = f"规则清单 + 模型语义审查（{settings.llm_model}）"
    else:
        mode_text = "仅规则清单（未配置 LLM_API_KEY）"
    print(f"模式：{mode_text}")
    if not settings.our_companies:
        print("[提醒] 未配置 OUR_COMPANIES，无法判断管辖地是否在我方所在地（可在 .env 补充）")
    if not settings.our_locations:
        print("[提醒] 未配置 OUR_LOCATIONS（如珠海），管辖地判断会跳过")

    try:
        result = review_contract(path, settings, force_rules=args.rules)
    except ParseError as exc:
        print(f"[X] {exc}")
        return 1

    out_dir = Path(args.out) if args.out else settings.contract_out_path
    paths = write_report(result, out_dir)

    print()
    print(f"综合风险：{result.overall}　（高 {len(result.high)} ｜ 中 {len(result.medium)} ｜ 低 {len(result.low)}）")
    print("\n--- 合同梗概 ---")
    labels = {
        "contract_type": "合同类型",
        "our_party": "我方主体",
        "counterparty": "对方主体",
        "subject": "合同内容",
        "amount": "合同金额",
        "payment_terms": "付款方式",
        "delivery_terms": "交付安排",
        "term": "合同期限",
    }
    for key, label in labels.items():
        value = str(result.summary.get(key) or "").strip()
        if value:
            print(f"  {label}：{value[:100]}")

    print("\n--- 风险清单 ---")
    if not result.findings:
        print("  （未发现明显风险）")
    for f in result.findings:
        print(f"[{f.level_label}] {f.title}" + (f"（{f.clause}）" if f.clause else ""))
        if f.issue:
            print(f"     问题：{f.issue}")
        if f.quote:
            print(f"     原文：「{f.quote.strip()[:120]}」")
        if f.suggestion:
            print(f"     建议：{f.suggestion}")

    print("\n--- 签约前必问对方的问题 ---")
    if result.questions:
        for i, q in enumerate(result.questions, 1):
            print(f"  {i}. {q}")
    else:
        print("  （暂无）")

    print("\n--- 检查清单 ---")
    present = sum(1 for c in result.checks if not c.missing)
    print(f"  共 {len(result.checks)} 项：已覆盖 {present}，缺失 {len(result.checks) - present}")
    if result.llm_error:
        print(f"  [提示] 未执行模型审查：{result.llm_error}")

    print(f"\n报告已生成：\n  Markdown：{paths['markdown']}\n  JSON 台账：{paths['json']}")
    if paths.get("docx"):
        print(f"  Word 报告：{paths['docx']}")
    elif paths.get("docx_error"):
        print(f"  [提示] Word 报告生成失败：{paths['docx_error']}")
    return 0


def cmd_preview_comment(args) -> int:
    """本地跑一遍审查，只打印审批单上会出现的评论原文（不写审批、不发消息）。"""
    import json as _json
    from pathlib import Path as _Path

    from ..contract import approval as approval_mod
    from ..contract.reviewer import review_contract
    from ..contract.store import ContractStore

    settings = Settings.load()
    path = _Path(args.file).expanduser()
    if not path.exists():
        print(f"[X] 文件不存在：{path}")
        return 1
    store = ContractStore(settings.db_file)
    try:
        result = review_contract(path, settings)
        review_id = store.save(
            file_name=path.name,
            file_path=str(path),
            overall=result.overall,
            high=len(result.high),
            medium=len(result.medium),
            low=len(result.low),
            summary=result.summary,
            findings=[f.to_dict() for f in result.findings],
            questions=result.questions,
            chat_id="",
            operator_open_id="",
            message_id="preview",
        )
        record = store.get(review_id) or {}
        name = args.approval_name or settings.contract_preview_approval_name
        text = approval_mod.build_comment_from_record(record, {"approval_name": name})
        print("=" * 72)
        print("审批评论样稿（一字不差就是审批人看到的）")
        print("=" * 72)
        print(text)
        print("=" * 72)
        print(f"共 {len(text.splitlines())} 行；综合风险 {result.overall}"
              f"（高 {len(result.high)} / 中 {len(result.medium)} / 低 {len(result.low)}）")
        print(f"模式：{result.mode}｜补充协议：{'是' if result.supplement else '否'}"
              + (f"（{result.supplement_evidence}）" if result.supplement_evidence else ""))
        if result.llm_error:
            print(f"⚠️ 模型层未生效：{result.llm_error}")
        if args.json:
            print(_json.dumps({
                "comment": text,
                "overall": result.overall,
                "high": len(result.high),
                "medium": len(result.medium),
                "low": len(result.low),
                "summary": result.summary,
                "findings": [f.to_dict() for f in result.findings],
                "questions": result.questions,
            }, ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


def cmd_contracts(args) -> int:
    """列出最近的合同审查记录。"""
    from ..contract.store import ContractStore

    settings = Settings.load()
    store = ContractStore(settings.db_file)
    rows = store.recent(args.limit)
    print("=" * 72)
    print("最近的合同审查记录")
    print("=" * 72)
    if not rows:
        print("  （还没有记录，先用 review-contract 或 contract-demo 审一份）")
        store.close()
        return 0
    for r in rows:
        created = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("created_ts", 0)))
        print(
            f"  {created}  {r['file_name'][:32]:<32} 风险{r['overall']:<2} "
            f"高{r['high']} 中{r['medium']} 低{r['low']}  {r['status']}  {r['id']}"
        )
    store.close()
    return 0
