"""合同机器人子命令：contract-bot、contract-demo。"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import ROOT, Settings


def cmd_contract_demo(args) -> int:
    """不连飞书，本地模拟一次「发合同 → 回卡片」的完整流程。"""
    from ..contract import cards as contract_cards
    from ..contract.render import write_report
    from ..contract.reviewer import review_contract
    from ..contract.store import ContractStore

    settings = Settings.load()
    path = Path(args.file) if args.file else ROOT / "data" / "sample_contract.txt"
    if not path.is_absolute() and not path.exists():
        alt = ROOT / path
        if alt.exists():
            path = alt

    print("=" * 72)
    print("合同审查机器人 · 本地演示（不连飞书）")
    print("=" * 72)
    print(f"文件：{path}")
    mode = "仅规则清单" if args.rules or not settings.use_llm else f"规则 + 模型（{settings.llm_model}）"
    print(f"模式：{mode}")

    result = review_contract(path, settings, force_rules=args.rules)
    paths = write_report(result, settings.contract_out_path)

    store = ContractStore(settings.db_file)
    findings = [f.to_dict() for f in result.findings]
    review_id = store.save(
        file_name=path.name,
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
        message_id="local-demo",
    )
    record = store.get(review_id) or {}
    card = contract_cards.review_card(record)

    card_path = settings.contract_out_path / "contract-card.json"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n综合风险：{result.overall}　（高 {len(result.high)} ｜ 中 {len(result.medium)} ｜ 低 {len(result.low)}）")
    print("\n--- 机器人会回的卡片（摘要） ---")
    print(f"  标题：合同预审 · {path.name}")
    print(f"  金额：{result.summary.get('amount') or '—'}")
    print(f"  对方：{result.summary.get('counterparty') or '—'}")
    for item in result.high[:3]:
        print(f"  🔴 {item.title}")
    print("  卡片里直接给出与审批评论一致的结论，不带按钮")

    print(f"\n卡片 JSON：{card_path}")
    print(f"报告：\n  Markdown：{paths.get('markdown')}\n  JSON：{paths.get('json')}")
    if paths.get("docx"):
        print(f"  Word：{paths['docx']}")
    print(f"\n数据库记录：review_id={review_id}（{store.stats()}）")
    store.close()
    return 0


def cmd_contract_bot(args) -> int:
    """启动合同审查机器人（飞书长连接）。"""
    from ..contract.bot import run

    settings = Settings.load()
    run(settings)
    return 0
