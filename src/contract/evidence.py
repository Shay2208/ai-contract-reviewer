"""通用工具：原文归一化、引用回验、句子切分，以及「模型结论该不该信」的几道校验。

这里的函数都不含业务阈值：规则层用它做定位，编排层用它做回验与去噪。
"""

from __future__ import annotations

import re

from .models import Finding

# ---------- 原文回验 ----------

_WS = re.compile(r"[\s\u3000]+")


def _normalize(text: str) -> str:
    """去掉所有空白（含全角空格），用于「引文是否真在原文里」的比对。"""
    return _WS.sub("", text)


def quote_in_text(quote: str, text: str, min_len: int = 8) -> bool:
    """引文回验：归一化后判断引文是否真的出现在原文里；过短的引文不可信，直接判否。"""
    q = _normalize(quote)
    if len(q) < min_len:
        return False
    return q in _normalize(text)


# ---------- 句子切分 ----------

_SENT_SPLIT = re.compile(r"[。；\n]")
_HEADING = re.compile(r"^(?:第[一二三四五六七八九十百零]+条(?:[^\n。；：:]{0,14})?[：:、.．]?\s*|\d+(?:\.\d+)*\s*[、.．:：]?\s*)")


def _clean_sentence(sent: str) -> str:
    """去掉句首的条款编号（「第四条」「3.1」之类），只留正文。"""
    return _HEADING.sub("", sent).strip()


def _sentences(text: str) -> list[str]:
    """按句号、分号、换行切句，丢掉空串。"""
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def _first_sentence_with(text: str, keywords: tuple[str, ...], limit: int = 120) -> str:
    """取第一句命中任一关键词的句子：去编号后截断到 limit 字，找不到返回空串。"""
    for sent in _sentences(text):
        if any(k in sent for k in keywords):
            cleaned = _clean_sentence(sent)
            if cleaned:
                return cleaned[:limit]
    return ""


# ---------- 模型结论的证据校验 ----------
#
# 模型最常见的幻觉是「明明写着，却报缺失」：抓住一句以冒号结尾的引导语（"支付至如下账户："），
# 就断言"未填写账户信息"，而账号就在下一行。这类结论必须靠规则否决，不能只靠提示词。

_ABSENCE_WORDS = (
    "缺失", "未提供", "未填写", "未写明", "未约定", "未明确", "没有约定", "未载明", "空白",
    # 规则层生成的缺失项用的是「合同中未检索到相关约定」，原先漏了这几个词，
    # 导致规则自己造的缺失条目一条都过不了这道闸（2026-11-16 修）
    "未检索到", "未找到", "未见", "缺少",
)

# (结论里出现的话题词, 证明该话题在正文里确有约定的正则)
_TOPIC_EVIDENCE: tuple[tuple[tuple[str, ...], str], ...] = (
    (("账户", "账号", "帐号", "户名", "开户行", "收款"), r"(账号|帐号)\s*[:：]?\s*\d{6,}"),
    (("付款节点", "付款时间", "付款期限", "支付时间"), r"(提前\s*\d+\s*个?工作日|\d+\s*个?工作日内|\d+\s*日内)[^。；]{0,12}支付"),
    (("管辖", "争议解决", "仲裁", "诉讼"), r"(人民法院|仲裁委员会|管辖)"),
    # 「验收」不能用「验收」二字兜底：合同里只要提到"验收后 7 个工作日"就算命中，
    # 会把"未约定验收标准/方式"这类真问题误杀。只认真正的验收约定证据。
    (("验收",), r"(验收标准|验收方式|验收合格标准|共同验收|签署验收单|验收单|验收报告)"),
    # 「逾期未提异议视为合格」这类结论，正文只要写了视为弃权/认可/合格就是幻觉
    (("异议", "视为合格", "视为弃权", "视为认可"), r"(视为弃权|视为认可|视为合格|逾期未提异议|未提出异议|未书面提出异议)"),
    (("违约金",), r"违约金"),
    (("保密",), r"保密"),
    (("知识产权",), r"(知识产权|著作权|专利权)"),
    (("发票", "含税", "税率", "税点"), r"(增值税|含税|发票|税点|税率)"),
    (("主体信息", "统一社会信用代码", "法定代表人", "住所"), r"(统一社会信用代码|法定代表人|住所|注册地址)"),
    (("规格", "型号", "数量"), r"(规格|型号|数量)"),
    (("质量标准", "质量要求"), r"(质量标准|国家标准|行业标准|验收标准)"),
    (("交付时间", "交货时间", "交付地点", "交货地点"), r"(交付|交货|到货|发货)"),
    (("合同金额", "价款"), r"(元/年|元整|总价|合同金额|价款)"),
)


def _contradicted_by_text(f: Finding, text: str) -> bool:
    """结论说"缺某项"，但正文里其实写了 → 判为模型幻觉，丢弃。"""
    claim = f"{f.title}{f.issue}"
    if not any(word in claim for word in _ABSENCE_WORDS):
        return False
    for topic_words, evidence in _TOPIC_EVIDENCE:
        if not any(word in claim for word in topic_words):
            continue
        if re.search(evidence, text):
            return True
    return False


# 明确不算风险的"审美类"措辞，出现即丢弃（提示词压不住时兜底）
_NOISE_WORDS = ("括号", "排版", "错别字", "标点", "语句不通", "表述不规范", "字体")


def _is_noise(f: Finding) -> bool:
    """纯排版、错别字这类"审美意见"不算风险，直接丢弃。"""
    claim = f"{f.title}{f.issue}"
    return any(word in claim for word in _NOISE_WORDS)


def _harmful_penalty_advice(f: Finding, our_side: str) -> bool:
    """建议「提高我方违约金／加重我方责任」的结论，方向反了，直接丢弃。

    实测事故：合同写「丙方违约赔偿甲方全部损失；甲方逾期付款按日万分之五」，
    这本来是对我方有利的条款，模型却报成"责任不对等"，还建议"提高甲方逾期付款的违约金比例"
    ——拿这条去汇报等于主动给自己加责任。
    """
    if not our_side:
        return False
    claim = f"{f.title}{f.issue}{f.suggestion}"
    pattern = (
        r"(?:提高|增加|上调|加重|提升)[^。；]{0,14}"
        + re.escape(our_side)
        + r"[^。；]{0,14}(?:违约金|赔偿责任|违约责任)"
    )
    return bool(re.search(pattern, claim))


# ---------- 立场判断 ----------


def _detect_our_side(text: str, settings) -> str:
    """判断我方在合同里是甲方还是乙方（按 OUR_COMPANIES 在文本中的位置就近取）。"""
    for name in getattr(settings, "our_companies", []) or []:
        if name and name in text:
            pos = text.find(name)
            window = text[max(0, pos - 80): pos + 80]
            if "甲方" in window:
                return "甲方"
            if "乙方" in window:
                return "乙方"
            return ""
    return ""


__all__ = [
    "_normalize",
    "quote_in_text",
    "_clean_sentence",
    "_sentences",
    "_first_sentence_with",
    "_contradicted_by_text",
    "_is_noise",
    "_harmful_penalty_advice",
    "_detect_our_side",
]
