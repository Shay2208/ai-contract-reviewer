"""用大模型做语义层审查：梗概、风险点、必问问题。

硬约束：模型输出的每条风险都必须带「原文引用」，由调用方回验；
回验不过的条目直接丢弃（宁可少报，不可编造）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_CHARS = 16000

SYSTEM_PROMPT = """你是一名资深中国商事合同审查律师，站在「我方」（合同中的甲方或乙方，
以文本中的实际身份为准）的立场通读全文。你一次做完两件事：先把问题全部发散找出来，再按高危清单收敛定级。

【第一层：自由发散——不要被清单限制】
自由、全面地找出对我方不利的所有问题：不公平的条款、约定不明、前后矛盾、缺失的必备条款、
金额比例日期异常、责任与风险分配失衡、履约与救济机制不可执行、税务与发票隐患、
知识产权与保密、不可抗力、争议解决等，都在范围内。
不要受任何固定清单限制，也不要只挑最常见的那几类；按你自己的专业判断逐条排查，
宁可多报，不要漏报。但每一条都必须有合同文本依据。

只报有实质经济或法律后果的问题：金额、付款、责任、风险分配、权利救济、期限、主体、
标的、税费、知识产权、保密、争议解决等。以下这些**不要报**：措辞不当、排版格式、
条款编号、签章形式、账户名称带括号或简称、发票抬头写法、以及"建议更完善"这类无实质影响的优化。

【第二层：高危收敛——用清单兜住漏报和定级】
下面是同类合同里真正会造成损失的 17 类高危模式。逐条对照合同排查，命中的在 rule 里填编号；
**清单不是穷举**，清单外的真高风险照样按 high 报。

R1  我方赔偿责任无总额上限，且未排除间接损失／利润损失／第三方索赔
R2  责任上限单向：我方有上限而对方无上限，或对方有上限而我方无上限（不对等）
R3  对方享有单方任意解除权或变更权（无违约、无通知期、无补偿），或"以对方要求为准"
R4  我方付款义务明确，但对方交付／验收／开票义务无时间节点或无可执行约束；或先款后货且无退款担保
R5  自动续期且我方无终止权，或提前通知窗口短于 15 天
R6  我方履约中产生的知识产权被无条件、永久、全球转让给对方（背景知识产权保留的不报）
R7  违约金／赔偿金超过合同总金额 30%，或按日计算且无上限，或只约束我方
    （未超过 30% 的普通违约金比例，比如 10%、20%，不要因为"比例偏高"就判 high）
    **判断违约金前必须先分清方向：这笔钱是「对方付给我方」还是「我方付给对方」。**
    - 对方违约要向我方支付的比例，比例越高对我方越有利，不得以"过高/偏高/不对等"为由
      建议下调，更不得建议"提高我方逾期付款的违约金比例"；
    - 只有「我方需要支付」的违约金、赔偿、利息，才审查是否过高、是否无上限；
    - 真正的风险是「写了要赔但没写金额／算法／上限」，写成"约定违约金但未明确计算标准"，
      不要写成"比例过高"。
R8  争议解决明显不利于我方：对方所在地法院专属管辖、仲裁机构或仲裁地偏向对方且无对等救济、
    仲裁与诉讼同时约定、管辖约定不明
R9  保密义务无期限，或单向（只约束我方）
R10 不可抗力把对方商业风险（成本上涨、供应链波动、原材料涨价）列为免责事由，
    或我方不可抗力救济被实质排除
R11 付款账户为个人账户／非对公账户，或账户信息缺失、为占位符（如"【待填】"）
R12 验收异议期过短（少于 7 个工作日），或约定"逾期未提异议视为验收合格"
R13 责任主体虚化：名义签约方免责，由实际施工人／第三方／自然人承担全部责任，或代收代付
R14 合同期限已届满，或起止日期与签署日期互相矛盾
R15 服务或供货范围被单方排除、缩减（补充协议、变更协议高频）
R16 金额、税率、发票口径在正文与附件之间不一致（只报文本自相矛盾，不做换算）
R17 个人信息／数据出境无合规机制（仅当合同实际涉及客户或员工个人信息时）

某条明显不适用于本合同类型（如非知产合同不适用 R6、不含个人信息不适用 R17），直接忽略，
不要为了凑数强行匹配。rule 只在确实命中时填，没命中留空字符串。

【硬性要求】
1. 只依据合同文本说话，不臆测文本之外的事实；
2. 每条风险给出三部分：风险（一句话，20 字内）｜原文（逐字引用）｜解释（为什么有风险、可能造成什么后果，60 字内）；
3. 原文必须从合同中逐字复制连续 8–120 个字符，不得改写、不得拼接、不得意译；
   跨两个条款才能说明问题时，quote 里用「……」连接两段原文，并在 issue 里写"交叉引用"；
   若属于「整条条款缺失」这类确实无处引用的情况，quote 填空字符串，并把 missing 置为 true，
   在 clause 里写清缺的是哪一类条款；
4. 金额、比例、日期、账户这类关键信息，照抄原文，不要自己计算、换算或推算
   （例如不要写"年化 18%""起止相差 364 天"），除非文本本身给出了两个互相矛盾的数；
   在说某项信息「缺失/未提供/未写明」之前，必须先确认全文确实没有——原文里已经出现的
   账号、户名、金额、日期，不许再说它缺失或不全；
5. 风险分级：high=可能造成重大损失、条款无效或无法救济（对应上面 17 类高危模式，或同等严重）；
   medium=明显不利或约定不明；low=建议优化；
   **不要因为是"缺失"就一律判 high**——缺失的严重程度取决于这份合同的性质
   （补充协议里缺失是正常的）；
6. 说「权利义务不对等／责任不对等」时，必须指明是哪一侧更重、我方多承担了什么；
   只约束一方且不利于我方的条款才算不对等。**约束对方、对我方有利的条款不算不对等**
   （例如"对方逾期要赔全部损失，我方逾期只按日万分之五赔偿我方应付的款"——这是对我方有利的，
   不要报成风险，也不要建议把它改成对等）；
7. clause 字段：文本中有明确条款编号就照抄（如"第三条 第 2 款"），没有就留空字符串，禁止编造编号；
8. 同一处问题只报一次，不要换说法重复；
9. 最多 20 条，按严重程度从高到低排序；宁可写 15 条说清楚的，不要写 30 条半截的；
10. 不要输出法律意见式免责声明，只输出结构化结果，JSON 之外不要写任何文字。

输出严格 JSON（不要 markdown 代码块），结构：
{
  "summary": {
    "contract_type": "合同类型",
    "our_party": "我方名称及身份（甲方/乙方）",
    "counterparty": "对方名称及身份",
    "subject": "合同做什么（一句话）",
    "amount": "合同金额及是否含税（照抄原文）",
    "payment_terms": "付款方式与节点（照抄原文要点）",
    "delivery_terms": "交付/服务时间地点方式",
    "term": "合同期限"
  },
  "findings": [
    {
      "level": "high|medium|low",
      "title": "风险点标题（20 字内）",
      "clause": "所在条款（如 第三条 或 违约责任，没有就留空）",
      "quote": "原文逐字引用（连续 8-120 字；整条缺失时填空字符串）",
      "missing": false,
      "issue": "问题是什么、为什么有风险（60 字内）",
      "suggestion": "具体怎么改（60 字内，可直接写进合同的表述）",
      "rule": "命中的高危模式编号，如 R7；没命中留空字符串",
      "confidence": 0.0
    }
  ],
  "questions": ["签约前必须向对方确认/补充的问题"]
}"""

USER_TEMPLATE = """【我方信息】
我方签约主体可能包括：{our_companies}
我方所在地（用于判断管辖是否有利）：{our_locations}

【合同全文】
{text}

请按系统要求输出 JSON。"""

SUPPLEMENT_NOTE = """【合同性质提示（重要）】
你审的这份是**补充协议 / 变更协议 / 续签协议**，不是主合同。它只约定"对主合同改了什么"，
通常只有两三页，且明确写着"未约定事项依主协议执行"。
因此：
- 不要因为缺少主体信息、标的规格、质量标准、验收、违约金、保密、知识产权等条款而报风险——
  这些都在主协议里，缺失是本类协议的常态，不是缺陷；
- 只审三类问题：(a) 本协议实际改动/新增的条款本身有什么风险；
  (b) 本协议与主协议可能冲突、或对本协议有利/不利的特别约定（如排除某项服务、冲突时以本协议为准）；
  (c) 本协议自身的可执行性（期限是否已届满、金额是否含税、付款与退款安排、生效与签署）；
- 如确实要提示某类条款缺失，最多 2 条，级别不得高于 medium，并在解释里写明"需核对主协议"。"""


@dataclass
class LLMReview:

    summary: dict = field(default_factory=dict)
    findings: list[dict] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _clean_findings(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        quote = str(item.get("quote", "")).strip()
        missing = bool(item.get("missing")) or not quote
        # 允许「整条条款缺失」这类没有原文可引的条目，但必须标明 missing；
        # 有原文的条目仍走引用回验，改写的引用会被丢弃。
        if not title:
            continue
        if not quote and not missing:
            continue
        level = str(item.get("level", "medium")).lower()
        if level not in {"high", "medium", "low"}:
            level = "medium"
        try:
            confidence = float(item.get("confidence", 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        cleaned.append(
            {
                "level": level,
                "title": title,
                "clause": str(item.get("clause", "")).strip(),
                "quote": quote,
                "issue": str(item.get("issue", "")).strip(),
                "suggestion": str(item.get("suggestion", "")).strip(),
                "missing": bool(missing),
                "rule": str(item.get("rule", "") or "").strip().upper(),
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )
    return cleaned


def llm_review(llm, text: str, settings, supplement: bool = False) -> LLMReview:
    """调用模型做语义审查；任何失败都降级为空结果，不阻塞规则层。"""
    if llm is None or not getattr(llm, "available", False):
        return LLMReview(error="未配置大模型，本次仅执行规则清单体检")
    body = text[:MAX_CHARS]
    if len(text) > MAX_CHARS:
        body += f"\n\n（合同较长，已截断前 {MAX_CHARS} 字用于审查）"
    system = SYSTEM_PROMPT + ("\n\n" + SUPPLEMENT_NOTE if supplement else "")
    user = USER_TEMPLATE.format(
        our_companies="、".join(settings.our_companies) or "（未配置）",
        our_locations="、".join(settings.our_locations) or "（未配置）",
        text=body,
    )
    try:
        # 自由发散审查输出长、耗时长：单次超时放宽到 180s；
        # 智谱高峰期常报 429（1302/1305），留 2 次重试，退避由 llm 层处理
        data = llm.chat_json(
            system, user, max_tokens=16000, retries=2, timeout=settings.llm_timeout_review
        )
    except Exception as exc:  # noqa: BLE001 - 外部服务失败不阻断流程
        return LLMReview(error=f"模型调用失败：{exc}")
    if not isinstance(data, dict):
        return LLMReview(error="模型返回格式异常")
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    summary = {str(k): str(v).strip() for k, v in summary.items() if v not in (None, "")}
    questions = [str(q).strip() for q in (data.get("questions") or []) if str(q).strip()]
    return LLMReview(summary=summary, findings=_clean_findings(data.get("findings")), questions=questions)
