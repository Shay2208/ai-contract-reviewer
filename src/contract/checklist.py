"""合同检查清单：固定条目 + 关键词/正则命中，保证可解释、可复核。

清单是给业务和法务看的「体检表」，不追求法律穷尽；命中即"有相关约定"，
是否充分由语义审查（LLM）和人工判断补足。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HIGH, MEDIUM, LOW = "high", "medium", "low"

LEVEL_LABEL = {HIGH: "高", MEDIUM: "中", LOW: "低"}

GROUPS = [
    "主体与标的",
    "价款与支付",
    "权利义务",
    "验收",
    "违约责任",
    "解除与退出",
    "争议解决",
    "担保",
    "其他条款",
]


@dataclass(frozen=True)
class CheckItem:
    id: str
    group: str
    title: str
    patterns: tuple[str, ...]
    risk: str = MEDIUM
    suggestion: str = ""
    must_ask: bool = False  # 缺失时列入「必问对方的问题」

    def search(self, text: str) -> str:
        """返回命中的原文片段（用于展示依据），没有则空串。"""
        for pat in self.patterns:
            m = re.search(pat, text)
            if m:
                start = max(0, m.start() - 12)
                end = min(len(text), m.end() + 12)
                return text[start:end].replace("\n", " ").strip()
        return ""


CHECKLIST: list[CheckItem] = [
    # 主体与标的
    CheckItem("parties", "主体与标的", "双方主体信息完整", ("统一社会信用代码", "法定代表人", "住所地?", "注册地址", "身份证号"),
              HIGH, "补齐双方名称、统一社会信用代码、法定代表人、住所，并与营业执照逐一核对", True),
    CheckItem("subject_name", "主体与标的", "标的名称明确", ("标的", "货物名称", "产品名称", "服务内容", "项目名称"),
              HIGH, "写明标的名称与具体内容，避免只写品类", True),
    CheckItem("spec_qty", "主体与标的", "规格型号与数量", ("规格", "型号", "数量", "清单", "技术参数"),
              HIGH, "以附件/清单明确规格型号、数量、单价，并约定附件与正文同等效力", True),
    CheckItem("quality", "主体与标的", "质量标准", ("质量标准", "国家标准", "行业标准", "技术标准", "技术协议"),
              HIGH, "约定质量标准（国标/行标/双方确认的样品或技术协议），并明确不达标如何处理"),
    CheckItem("delivery", "主体与标的", "交付时间、地点、方式", ("交货", "交付", "运输", "到货", "发货", "交付地点"),
              HIGH, "写明交付时间、地点、运输与风险转移（签收/验收后转移）"),
    CheckItem("term", "主体与标的", "合同期限", ("有效期", "合同期限", "服务期限", "合作期限"),
              LOW, "补充合同起止日期或服务期限"),
    # 价款与支付
    CheckItem("amount", "价款与支付", "合同金额", ("合同总价", "合同金额", "总金额", "总价款", "价款合计", "人民币"),
              HIGH, "写明金额（含税/不含税）与大写金额", True),
    CheckItem("tax_invoice", "价款与支付", "含税与发票", ("含税", "不含税", "税率", "发票", "增值税"),
              HIGH, "明确金额是否含税、发票类型（增值税专票/普票）与开票时间", True),
    CheckItem("payment_terms", "价款与支付", "付款节点与条件", ("预付", "首付", "付款方式", "账期", "结算方式", "付款节点", "验收后"),
              HIGH, "写明付款节点、比例、触发条件与期限，避免只写「验收后付款」而无期限", True),
    CheckItem("bank_account", "价款与支付", "付款账户为对公账户", ("对公", "开户行", "开户银行", "账户名称", "银行账号", "银行账户"),
              HIGH, "写明收款方全称、开户行、对公账号，并约定账户变更须书面确认", True),
    # 权利义务
    CheckItem("rights", "权利义务", "双方权利义务", ("双方权利义务", "甲方的权利", "乙方的权利", "权利和义务", "甲方义务", "乙方义务"),
              MEDIUM, "逐项列明双方义务，避免只约束一方"),
    CheckItem("confidentiality", "权利义务", "保密条款", ("保密",), MEDIUM, "补充保密范围、期限与违约责任"),
    CheckItem("ip", "权利义务", "知识产权归属", ("知识产权", "著作权", "专利权", "商标权", "商标"), MEDIUM,
              "明确交付成果/产品涉及的知识产权归属与授权范围"),
    # 验收
    CheckItem("acceptance_standard", "验收", "验收标准", ("验收标准", "验收方式", "验收合格", "验收条件", "验收流程"),
              HIGH, "写明验收项目、标准、方法（抽检/全检）与不合格处理", True),
    CheckItem("acceptance_deadline", "验收", "验收期限", ("验收期", "验收期限", r"\d+\s*(?:个)?(?:工作)?日内验收", r"收到.{0,10}日内"),
              MEDIUM, "约定明确的验收期限（如到货后 7 日内）"),
    CheckItem("deemed_pass", "验收", "逾期未提异议视为合格", ("视为合格", "视为验收合格", "未提出异议", "逾期未提出异议", "视为认可", "视为验收通过"),
              HIGH, "加入「验收期内未书面提出异议视为验收合格」，避免无限期扯皮", True),
    # 违约责任
    CheckItem("penalty", "违约责任", "违约金/逾期责任", ("违约金", "滞纳金", "逾期付款", "逾期交货", "逾期交付"),
              HIGH, "写明违约金计算方式与比例，双方对等", True),
    CheckItem("penalty_cap", "违约责任", "违约金上限", (r"违约金.{0,20}(?:上限|不超过|最高)", "最高不超过", "封顶", "累计不超过"),
              MEDIUM, "约定违约金累计上限（如不超过合同总额的 20%–30%）"),
    CheckItem("damages", "违约责任", "损失赔偿范围", ("赔偿", "损失"), MEDIUM,
              "明确赔偿范围是否含间接损失/可得利益，避免敞口"),
    # 解除与退出
    CheckItem("termination", "解除与退出", "解除/终止条件", ("解除", "终止"), HIGH,
              "列明可解除情形（根本违约、逾期超过 X 日、不可抗力持续等）", True),
    CheckItem("termination_settle", "解除与退出", "解除后的结算与退出", ("解除后", "结算", "返还", "退出", "清理"),
              MEDIUM, "约定解除后的结算、已付款返还、货物/资料退回与过渡期安排"),
    # 争议解决
    CheckItem("dispute", "争议解决", "争议解决方式", ("仲裁", "人民法院", "诉讼", "管辖"), HIGH,
              "在「仲裁」与「法院诉讼」中二选一，写明具体机构", True),
    CheckItem("jurisdiction", "争议解决", "管辖地在我方所在地", ("管辖", "仲裁委员会", "人民法院"), MEDIUM,
              "争取约定我方所在地法院/仲裁机构；对方所在地需评估成本"),
    CheckItem("governing_law", "争议解决", "适用法律", ("适用中华人民共和国法律", "适用法律"), LOW, "约定适用中华人民共和国法律"),
    # 担保
    CheckItem("guarantee", "担保", "担保措施", ("保证人", "抵押", "质押", "担保", "连带责任保证"), MEDIUM,
              "大额或预付款交易，争取定金/履约保证金/保证人/抵押等担保"),
    CheckItem("deposit", "担保", "定金或保证金", ("定金", "履约保证金", "保证金"), LOW,
              "约定定金或履约保证金金额、退还条件与抵扣方式"),
    # 其他条款
    CheckItem("force_majeure", "其他条款", "不可抗力", ("不可抗力",), LOW, "补充不可抗力定义、通知义务与后果"),
    CheckItem("notice", "其他条款", "通知与送达", ("送达", "通知地址", "电子邮件", "联系方式", "通讯地址"), LOW,
              "约定通知方式、送达地址与地址变更通知义务"),
    CheckItem("effectiveness", "其他条款", "生效条件", ("生效", "签字盖章", "盖章后生效"), MEDIUM,
              "写明生效条件（双方签字盖章）与份数"),
    CheckItem("attachments", "其他条款", "附件清单", ("附件",), LOW, "列明附件名称并约定与正文同等效力"),
    CheckItem("signature", "其他条款", "签署与盖章", ("盖章", "签字", "法定代表人", "授权代表"), MEDIUM,
              "确认签署页、盖章要求与授权代表权限"),
]

CHECK_BY_ID = {item.id: item for item in CHECKLIST}


@dataclass
class CheckResult:
    item: CheckItem
    status: str  # present | missing
    evidence: str = ""

    @property
    def missing(self) -> bool:
        return self.status == "missing"


def run_checks(text: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    for item in CHECKLIST:
        hit = item.search(text)
        results.append(CheckResult(item=item, status="present" if hit else "missing", evidence=hit))
    return results


def missing_items(results: list[CheckResult], level: str | None = None) -> list[CheckResult]:
    return [r for r in results if r.missing and (level is None or r.item.risk == level)]


def group_results(results: list[CheckResult]) -> dict[str, list[CheckResult]]:
    grouped: dict[str, list[CheckResult]] = {g: [] for g in GROUPS}
    for r in results:
        grouped.setdefault(r.item.group, []).append(r)
    return grouped
