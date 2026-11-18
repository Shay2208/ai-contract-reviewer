"""审批「用印申请」接入：解析表单 → 判定是否合同 → 下载附件 → 预审 → 回写评论。

飞书审批实例详情里的 form 是一段 JSON 字符串，控件值形如：
    {"id": "...", "custom_id": "...", "name": "是否合同文件", "type": "checkbox", "value": ["是"]}
    {"id": "...", "name": "合同附件", "type": "attachmentV2", "ext": "'合同.docx'", "value": ["https://..."]}

本模块只做「识别 + 组装 + 回写」，真正的合同审查复用 src.contract.reviewer。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from . import ocr as ocr_mod
from .quality import is_text_quality
from .render import write_report
from .reviewer import ReviewResult, review_contract
from .store import ContractStore

log = logging.getLogger(__name__)

CHECKBOX_TYPES = {"checkbox", "checkboxV2", "radio", "radioV2", "select"}
ATTACH_TYPES = {"attachmentV2", "attachment", "imageV2", "image"}
CONTRACT_WORDS = ("合同", "协议", "用印", "協議", "合約")
# 用印申请这类「天然全是合同」的审批定义：表单里没有「是否合同」勾选题，
# 只要审批名/定义命中这些词就整体视为合同。
ALWAYS_CONTRACT_WORDS = ("用印", "用章", "盖章", "用印申請", "蓋章")
# 排除明显的非合同控件，防止「合同類型/合同編号」这类字段被误当附件或勾选项。
NON_CONTRACT_FIELD_WORDS = ("类型", "類型", "编号", "編號", "事由", "日期", "单位", "單位")
NEGATIVE_WORDS = ("非合同", "无合同", "不需要", "否", "无")
POSITIVE_WORDS = ("是", "有", "需要", "合同", "协议", "用印", "同意")


def parse_form(raw: Any) -> list[dict[str, Any]]:
    """把实例详情里的 form 字段（JSON 字符串或列表）解析成控件列表。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [w for w in raw if isinstance(w, dict)]
    if isinstance(raw, dict):
        return [raw]
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    return [w for w in data if isinstance(w, dict)] if isinstance(data, list) else []


def _values(widget: dict[str, Any]) -> list[str]:
    value = widget.get("value")
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def find_contract_widget(widgets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """找「是否合同文件」这类勾选项（也兼容用单行文本填「是/否」的表单）。"""
    for widget in widgets:
        name = str(widget.get("name") or "")
        widget_type = widget.get("type")
        raw_values = _values(widget)
        values = " ".join(raw_values)
        if any(word in name for word in CONTRACT_WORDS) and not any(
            word in name for word in NON_CONTRACT_FIELD_WORDS
        ):
            # 名称含「合同」但内容很长 → 那是正文，不是勾选项
            if widget_type in CHECKBOX_TYPES or max((len(v) for v in raw_values), default=0) <= 30:
                return widget
        if widget_type in CHECKBOX_TYPES and any(word in values for word in CONTRACT_WORDS):
            if name in ("", "类型", "文件类型"):
                return widget
    return None


def find_contract_text(widgets: list[dict[str, Any]]) -> tuple[str, str]:
    """找出表单里贴的合同正文（没附件时的兜底）。返回 (正文, 控件名)。"""
    best = ("", "")
    for widget in widgets:
        widget_type = widget.get("type")
        if widget_type not in ("textarea", "input", "text"):
            continue
        name = str(widget.get("name") or "")
        text = "\n".join(_values(widget))
        if len(text) < 200:
            continue
        if name and not any(word in name for word in CONTRACT_WORDS):
            continue
        if len(text) > len(best[0]):
            best = (text, name)
    return best


def approval_always_contract(approval_name: str) -> bool:
    """「用印申请」这类审批定义天然全是合同，不需要表单勾选判断。"""
    return any(word in (approval_name or "") for word in ALWAYS_CONTRACT_WORDS)


def is_contract(widgets: list[dict[str, Any]], approval_name: str = "") -> tuple[bool, str]:
    """判断这张审批单是不是合同类。返回 (是否合同, 依据说明)。"""
    # 真实「用印申请（合同/非合同）」：先用「合同类型」字段判，非合同直接跳过
    for widget in widgets:
        name = str(widget.get("name") or "")
        if "合同类型" in name or "合同類型" in name:
            text = " ".join(_values(widget)).strip()
            if text and "非合同" in text:
                return False, f"「{name}」为「{text}」，非合同，跳过"
            if text and any(word in text for word in CONTRACT_WORDS):
                return True, f"「{name}」为「{text}」，按合同审查"
    if approval_always_contract(approval_name):
        return True, f"「{approval_name}」为合同类审批，整单默认审查"
    widget = find_contract_widget(widgets)
    if widget is None:
        return False, "表单里没有找到「是否合同」勾选项，跳过"
    values = _values(widget)
    text = " ".join(values).strip()
    if not values:
        return False, f"「{widget.get('name')}」未勾选，跳过"
    if any(word in text for word in NEGATIVE_WORDS) and not any(
        word in text for word in CONTRACT_WORDS
    ):
        return False, f"「{widget.get('name')}」勾选为「{text}」，非合同，跳过"
    if any(word in text for word in POSITIVE_WORDS):
        return True, f"「{widget.get('name')}」勾选为「{text}」"
    return False, f"「{widget.get('name')}」勾选为「{text}」，未识别为合同，跳过"


def find_attachments(widgets: list[dict[str, Any]]) -> list[dict[str, str]]:
    """取出附件控件里的文件（name/url）。

    真实用印申请有两个附件：「合同未签署电子原件」和「归档（已签署文件）」。
    以未签署的原件为审查对象；只有它没有时才退回其它附件。
    """
    files: list[dict[str, str]] = []
    for widget in widgets:
        if widget.get("type") not in ATTACH_TYPES:
            continue
        urls = _values(widget)
        if isinstance(widget.get("value"), str):
            urls = [u for u in str(widget.get("value")).split(",") if u]
        names_raw = widget.get("ext") or ""
        names = [n.strip().strip("'\"") for n in str(names_raw).split(",") if n.strip()]
        widget_name = str(widget.get("name") or "")
        for index, url in enumerate(urls):
            url = url.strip()
            if not url.startswith("http"):
                continue
            name = names[index] if index < len(names) else f"附件{index + 1}"
            files.append({"name": name, "url": url, "widget": widget_name})
    # 优先未签署原件，排除归档件（已盖章的扫描件不该再走预审）
    primary = [
        f
        for f in files
        if ("未签署" in f["widget"] or "电子原件" in f["widget"])
    ]
    if primary:
        return primary
    rest = [f for f in files if not ("归档" in f["widget"] or "已签署" in f["widget"])]
    return rest or files


def safe_name(name: str) -> str:
    base = Path(str(name)).name or "attachment"
    return re.sub(r'[\\/:*?"<>|]+', "_", base)


def build_comment(result: ReviewResult, instance: dict[str, Any], max_items: int = 3) -> str:
    """把审查结果拼成一条可以贴进审批评论的文本。"""
    high = result.high
    middle = result.medium
    lines = [
        f"【AI 合同预审】{instance.get('approval_name') or '用印申请'}",
        f"综合风险：{result.overall}（高风险 {len(high)} 条 / 中风险 {len(middle)} 条 / 低风险 {len(result.low)} 条）",
    ]
    summary = result.summary or {}
    if summary.get("amount"):
        lines.append(f"合同金额：{summary['amount']}")
    if summary.get("counterparty"):
        lines.append(f"对方主体：{summary['counterparty']}")
    shown = 0
    for finding in sorted(result.findings, key=lambda f: {"high": 0, "medium": 1, "low": 2}[f.level]):
        if finding.level == "low" or shown >= max_items:
            continue
        shown += 1
        lines.append(f"{shown}. [{finding.level_label}] {finding.title}")
        if finding.issue:
            lines.append(f"   - {finding.issue}")
        if finding.quote:
            quote = finding.quote if len(finding.quote) <= 80 else finding.quote[:80] + "…"
            lines.append(f"   - 原文：「{quote}」")
    if result.questions:
        lines.append("签约前建议确认：" + "；".join(result.questions[:3]))
    lines.append("以上由 AI 按检查清单自动生成，仅供参考，最终以法务意见为准。")
    return "\n".join(lines)


def _parse_date_value(raw: str) -> Any:
    """审批表单日期有两种：毫秒时间戳（"1794474457000"）和 ISO 串（"2026-10-26T00:00:00+08:00"）。"""
    from datetime import datetime

    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        digits = raw[:13] if len(raw) >= 13 else raw
        try:
            return datetime.fromtimestamp(int(digits) / 1000)
        except (ValueError, OSError):
            return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def form_consistency(widgets: list[dict[str, Any]], contract_text: str) -> list[str]:
    """表单关键字段 vs 合同正文的一致性核对（纯规则，不用模型）。

    返回备注列表：全部一致时是 ["表单关键字段与合同正文一致"]，否则每条不一致一项。
    """

    text = re.sub(r"\s+", "", contract_text or "")
    date_words = ("签订日期", "簽訂日期", "结束日期", "結束日期")
    notes: list[str] = []
    for widget in widgets:
        name = str(widget.get("name") or "")
        values = _values(widget)
        if not values:
            continue
        value = values[0].strip()
        if ("对方单位" in name or "對方單位" in name) and len(value) >= 4:
            key = re.sub(r"\s+", "", value)
            if key not in text:
                notes.append(f"对方单位「{value}」未在合同正文找到")
        elif any(w in name for w in date_words) and value:
            dt = _parse_date_value(value)
            if dt is None:
                continue
            variants = (
                f"{dt.year}年{dt.month}月{dt.day}日",
                f"{dt.year}-{dt.month:02d}-{dt.day:02d}",
                f"{dt.year}/{dt.month:02d}/{dt.day:02d}",
                f"{dt.year}.{dt.month:02d}.{dt.day:02d}",
            )
            if not any(v in text for v in variants):
                notes.append(f"{name}「{dt:%Y-%m-%d}」未在合同正文找到")
        elif ("自动顺延" in name or "自動順延" in name) and value in ("是", "否"):
            if "顺延" not in text and "順延" not in text:
                notes.append(f"表单{name}={value}，但合同正文未见顺延条款")
    return notes or ["表单关键字段与合同正文一致"]


def process_instance(
    api: Any,
    settings: Any,
    store: ContractStore,
    instance_code: str,
    *,
    post_comment: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """处理一个审批实例：识别合同 → 下载附件 → 预审 → 回写评论。

    返回 {ok, skipped, reason, instance, results, comment_id, reports}
    """
    instance = api.get_instance(instance_code)
    widgets = parse_form(instance.get("form"))
    approval_name = str(instance.get("approval_name") or "")
    ok, reason = is_contract(widgets, approval_name=approval_name)
    if not ok and not force:
        return {"ok": False, "skipped": True, "reason": reason, "instance": instance}

    files = find_attachments(widgets)
    if not files:
        text, text_name = find_contract_text(widgets)
        if not text:
            return {
                "ok": False,
                "skipped": True,
                "reason": "表单里没有找到可下载的附件（attachmentV2/attachment），也没有贴合同正文",
                "instance": instance,
            }
        upload_dir = Path(settings.contract_upload_path)
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / f"approval-{instance_code[:8]}-form-text.txt"
        target.write_text(text, "utf-8")
        files = [{"name": text_name or "表单合同正文", "url": "", "widget": text_name, "path": str(target)}]

    out_dir = Path(settings.contract_out_path)
    upload_dir = Path(settings.contract_upload_path)
    results: list[dict[str, Any]] = []

    # 先把附件落盘，再判断是否需要 OCR：占位评论必须在识别开始前发出去，
    # 否则审批人看到空评论会以为 AI 没跑，可能直接把单批了。
    prepared: list[dict[str, Any]] = []
    for item in files:
        target = Path(item["path"]) if item.get("path") else None
        if target is None:
            target = upload_dir / f"approval-{instance_code[:8]}-{safe_name(item['name'])}"
            try:
                api.download_url(item["url"], target)
            except Exception as exc:  # noqa: BLE001
                results.append({"file": item["name"], "ok": False, "error": f"下载失败：{exc}"})
                continue
        prepared.append({"name": item["name"], "path": target})

    placeholder_id = ""
    if post_comment and getattr(settings, "contract_ocr_enabled", False):
        scans = [p for p in prepared if ocr_mod.needs_ocr(p["path"])]
        if scans:
            pages = sum(ocr_mod.page_count(p["path"]) for p in scans) or 1
            minutes = max(1, round(ocr_mod.estimate_seconds(pages) / 60))
            note = (
                f"【AI 合同预审】已收到文件，正在识别扫描件（共 {pages} 页，约 {minutes} 分钟），"
                "识别完成后会再补一条审查结论，请稍候再审批。"
            )
            placeholder_id, _ = _post_comment(api, settings, instance, instance_code, note)
            if placeholder_id:
                log.info("已写占位评论：instance=%s 页数=%s", instance_code, pages)

    for item in prepared:
        target = item["path"]
        try:
            result = review_contract(target, settings)
        except Exception as exc:  # noqa: BLE001
            results.append({"file": item["name"], "ok": False, "error": f"解析失败：{exc}"})
            continue
        paths = write_report(result, out_dir)
        review_id = store.save(
            file_name=item["name"],
            file_path=str(target),
            overall=result.overall,
            high=len(result.high),
            medium=len(result.medium),
            low=len(result.low),
            summary=result.summary,
            findings=[f.to_dict() for f in result.findings],
            questions=result.questions,
            report_md=str(paths.get("markdown", "")),
            report_json=str(paths.get("json", "")),
            report_docx=str(paths.get("docx", "")),
            chat_id=str(instance.get("open_id") or ""),
            operator_open_id=str(instance.get("open_id") or ""),
            message_id=str(instance_code),
        )
        results.append(
            {
                "file": item["name"],
                "ok": True,
                "review_id": review_id,
                "overall": result.overall,
                "high": len(result.high),
                "medium": len(result.medium),
                "file_path": str(target),
                "reports": paths,
            }
        )

    good = [r for r in results if r.get("ok")]
    comment_id = ""
    comment_text = ""
    comment_error = ""
    if good and post_comment:
        # 只把最严重的一份写进评论
        worst = max(good, key=lambda r: r["high"] * 10 + r["medium"])
        review = store.get(worst["review_id"])
        mismatches: list[str] | None = None
        try:
            contract_text = Path(worst["file_path"]).read_text("utf-8", errors="ignore")
            mismatches = form_consistency(widgets, contract_text)
        except OSError:
            pass
        full = bool(getattr(settings, "contract_comment_full", True))
        comment_text = build_comment_from_record(
            review, instance, mismatches=mismatches, full=full
        )
        # 全量评论可能过万字，超长按行拆成多条，避免 4002 comment service add err
        chunks = split_comment(comment_text)
        posted: list[str] = []
        for index, chunk in enumerate(chunks, 1):
            cid, err = _post_comment(api, settings, instance, instance_code, chunk)
            if err:
                comment_error = err
                break
            posted.append(cid)
            if len(chunks) > 1:
                log.info("审批评论已写入第 %d/%d 条：instance=%s", index, len(chunks), instance_code)
        comment_id = ",".join(posted)
        if comment_error:
            comment_text += f"\n（评论写入失败：{comment_error}）"

    return {
        "ok": bool(good),
        "skipped": False,
        "reason": reason,
        "instance": instance,
        "results": results,
        "comment_id": comment_id,
        "comment_error": comment_error,
        "comment": comment_text,
        "placeholder_id": placeholder_id,
    }


def _post_comment(
    api,
    settings,
    instance: dict[str, Any],
    instance_code: str,
    text: str,
) -> tuple[str, str]:
    """写审批评论，返回 (comment_id, error)。

    飞书审批评论必须挂真人，且这个人必须是该审批单的参与人，否则回
    60009 user has no permission（实测：配置的机器人管理员不在别人提交的单里就会被拒）。
    按优先级依次尝试：发起人（用户拍板优先，读起来就是"申请人提交时附带的 AI 预审"）
    → 配置的管理员 → 当前待办审批人，任一成功即止。
    """
    candidates: list[tuple[str, str]] = []
    if instance.get("open_id"):
        candidates.append((str(instance["open_id"]), "open_id"))
    elif instance.get("user_id"):
        candidates.append((str(instance["user_id"]), "user_id"))
    configured = str(getattr(settings, "contract_comment_open_id", "") or "")
    if configured:
        candidates.append((configured, "open_id"))
    for task in instance.get("task_list") or []:
        if task.get("status") == "PENDING" and task.get("open_id"):
            candidates.append((str(task["open_id"]), "open_id"))

    tried: set[tuple[str, str]] = set()
    for uid, uid_type in candidates:
        if not uid or (uid, uid_type) in tried:
            continue
        tried.add((uid, uid_type))
        try:
            comment_id = api.create_comment(
                instance_code, text, user_id=uid, user_id_type=uid_type
            )
            return comment_id, ""
        except Exception as exc:  # noqa: BLE001
            log.warning("审批评论写入失败（署名 %s，类型 %s）：%s", uid, uid_type, exc)
    return "", "评论写入失败（无可用的评论人）"


def _as_json(value: Any, default: Any) -> Any:
    """store.get 已把 *_json 字段解析过，这里兼容原始字符串。"""
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value or "null") or default
    except (TypeError, json.JSONDecodeError):
        return default


def _verdict_line(high: list[dict], medium: list[dict], low: list[dict]) -> str:
    """结论行：给一句能直接照做的判断，而不是只报计数。

    行政视角（2026-11-16 反馈）："结论：高风险（高5/中11/低5）"只是数数，
    她真正要知道的是"今天这单能不能过、要先去问谁什么"。
    """
    counts = f"高 {len(high)} / 中 {len(medium)} / 低 {len(low)}"
    if len(high) >= 2:
        verdict = "暂缓用印"
    elif len(high) == 1:
        verdict = "建议先确认再用印"
    elif len(medium) >= 3:
        verdict = "可先用印，留意下列问题"
    else:
        verdict = "可按常规用印"

    focus = high if high else medium
    titles = []
    for finding in focus[:3]:
        name = _clip(finding.get("title"), 14)
        if name and name not in titles:
            titles.append(name)
    tail = f"｜先确认：{'、'.join(titles)}" if titles else ""
    return f"结论：{verdict}（{counts}）{tail}"


def build_comment_from_record(
    record: dict[str, Any],
    instance: dict[str, Any],
    mismatches: list[str] | None = None,
    max_items: int = 4,
    full: bool = True,
) -> str:
    """审批评论：一条风险一行，格式「风险｜原文｜解释」。

    行政要求模型不受固定清单限制自由审查，评论里要能看到三段依据：
    风险是什么、合同原文怎么写、为什么有风险。整条缺失时写「合同未约定」。

    full=True（默认）：列出全部风险，评论区就能看全，不用再点文档
    （同事要求）。实测飞书评论正文 10000 字可以、12000 字报
    ``4002 comment service add err``，超长由 split_comment 拆成多条。
    full=False：只列前 max_items 条并压到 12 行以内（早期极简版）。
    """
    all_findings = _as_json(record.get("findings_json"), [])
    # 原件质量问题（OCR 乱码、条款编号混乱）不进风险行：评论区每一行都得是
    # "该不该盖章"的判断依据。它们单独压成一行提示。
    quality = [f for f in all_findings if is_text_quality(f)]
    findings = [f for f in all_findings if not is_text_quality(f)]
    questions = _as_json(record.get("questions_json"), [])
    summary = _as_json(record.get("summary_json"), {})
    high = [f for f in findings if f.get("level") == "high"]
    medium = [f for f in findings if f.get("level") == "medium"]
    low = [f for f in findings if f.get("level") == "low"]
    title = record.get("file_name") or "合同"
    lines = [
        f"【AI 合同预审】{instance.get('approval_name') or '用印申请'}｜{title}",
        _verdict_line(high, medium, low),
    ]
    facts = []
    if summary.get("amount"):
        facts.append(f"金额 {summary['amount']}")
    if summary.get("counterparty"):
        facts.append(f"对方 {summary['counterparty']}")
    if facts:
        lines.append("｜".join(facts))
    if mismatches:
        shown_mm = mismatches[:2]
        prefix = "表单核对：一致" if mismatches == ["表单关键字段与合同正文一致"] else "表单核对不符："
        if mismatches == ["表单关键字段与合同正文一致"]:
            lines.append(prefix)
        else:
            lines.append(prefix + "；".join(shown_mm) + ("…" if len(mismatches) > 2 else ""))
    order = {"high": 0, "medium": 1, "low": 2}
    limit = len(findings) if full else max_items
    shown = 0

    def rank(finding: dict[str, Any]) -> tuple[int, int]:
        """同级别里优先带原文引用的语义风险，「缺少某条款」这类通用项排后面。

        否则 Top3 会被「缺少双方权利义务/保密条款」占满，真正值钱的
        「付款节点风险」「违约金失衡」反而挤不进来。
        """
        has_quote = 0 if str(finding.get("quote") or "").strip() else 1
        return (order.get(finding.get("level"), 9), has_quote)

    # 全量模式下字数够用，原文与解释各放宽到 60 字，读起来更像人话
    quote_limit = 60 if full else 40
    issue_limit = 60 if full else 45
    for finding in sorted(findings, key=rank):
        if shown >= limit:
            break
        shown += 1
        label = {"high": "高", "medium": "中", "low": "低"}.get(finding.get("level"), "")
        title = (finding.get("title") or "").strip()
        quote = _clip(finding.get("quote"), quote_limit)
        issue = _clip(finding.get("issue"), issue_limit)
        quote_part = f"原文「{quote}」" if quote else "原文（合同未约定）"
        lines.append(f"{shown}. [{label}] {title}｜{quote_part}｜解释：{issue or '—'}")
    if questions:
        lines.append("签约前必问：" + "；".join(_clip(q, 30) for q in questions[:2]))
    if quality:
        lines.append(f"原件质量：存在 {len(quality)} 处文件问题（如 {_clip(quality[0].get('title'), 20)}），建议索取清晰原件。")
    lines.append("AI 自动生成，仅供参考，最终以法务意见为准。")
    if not full:
        lines = lines[:12]
    return "\n".join(lines)


_COMMENT_MAX_CHARS = 8000  # 实测 10000 字可写入，留 2000 字余量给多字节与后续拼接


def split_comment(text: str, max_chars: int = _COMMENT_MAX_CHARS) -> list[str]:
    """把超长评论按行拆成多条，每条都在飞书允许的字数内。

    第 1 条带完整抬头，后续每条抬头加「（续 i/n）」，审批人不会看丢是哪一份合同。
    正常一份合同（12~20 条结论）约 2500~5000 字，通常只有 1 条。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    lines = text.split("\n")
    header, body = lines[0], lines[1:]
    budget = max(600, max_chars - len(header) - 20)
    chunks: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for line in body:
        add = len(line) + 1
        if cur and size + add > budget:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(line)
        size += add
    if cur:
        chunks.append(cur)
    total = len(chunks)
    if total <= 1:
        return [text]
    return [
        "\n".join(
            [f"{header}（{i}/{total}）" if i == 1 else f"{header}（续 {i}/{total}）"] + chunk
        )
        for i, chunk in enumerate(chunks, 1)
    ]


def _clip(value: Any, limit: int) -> str:
    """压成一行并截断，保证评论里一条风险不串行。"""
    text = " ".join(str(value or "").split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def extract_instance_code(text: str) -> str:
    """从审批链接或文本里取 instance_code。"""
    match = re.search(r"(?:instance_code|instance_id)=([A-Za-z0-9_\-]+)", text)
    if match:
        return match.group(1)
    match = re.search(
        r"\b([0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})\b", text, re.IGNORECASE
    )
    return match.group(1) if match else text.strip()


def subscribe_instance_event(settings: Any, approval_code: str) -> dict[str, Any]:
    """订阅某个审批定义的状态变更事件。

    实测（2026-11-13）：应用身份 tenant_access_token + POST
    /approval/v4/approvals/{approval_code}/subscribe 即可，返回 code=0 success；
    重复订阅返回 1390007 subscription existed，视为已订阅。
    """
    from ..feishu_api import FeishuAPI

    api = FeishuAPI(settings.app_id, settings.app_secret)
    return api.subscribe_instance_event(approval_code)

