"""主体与账户：付款是否流向个人账户（规则 4）、责任主体是否被虚化（规则 13）。

另外提供「对方主体名截断」工具：扫描件的印章、税号、开户行残渣不能混进对方名称。
"""

from __future__ import annotations

import re

from ..models import HIGH, Finding


def rule_personal_account(text: str) -> list[Finding]:
    """规则 4：付款/收款指向个人账户、个人卡或第三方支付平台。"""
    findings: list[Finding] = []
    personal = re.search(r"(?:付款|收款|转账|账户)[^。；\n]{0,40}(?:个人账户|私人账户|个人卡|支付宝|微信)", text)
    if personal:
        findings.append(
            Finding(
                level=HIGH,
                title="付款可能进入个人账户",
                clause="价款与支付",
                quote=personal.group(0).strip(),
                issue="向个人账户/第三方支付平台付款，无法证明向合同相对方履行，税务与追偿风险都高。",
                suggestion="改为「付款至乙方对公账户：户名 XXX，开户行 XXX，账号 XXX」，并约定账户变更须书面盖章确认。",
            )
        )
    return findings


def rule_hollow_subject(text: str) -> list[Finding]:
    """规则 13：责任主体虚化——名义签约方免责，或由实际施工人等第三方独担（R13）。

    单轮模型会漏这一类，用规则兜底（实测：两轮时能抓到，单轮常漏）。
    """
    findings: list[Finding] = []
    hollow = re.search(
        r"[^。；\n]{0,80}(?:唯一(?:的)?(?:承担|责任承担)?主体|唯一责任承担主体)[^。；\n]{0,40}"
        r"|[^。；\n]{0,60}(?:不承担|不负责)[^。；\n]{0,20}(?:施工|履行|质量|安全|保修)[^。；\n]{0,20}责任[^。；\n]{0,20}",
        text,
    )
    if hollow:
        findings.append(
            Finding(
                level=HIGH,
                title="责任主体虚化",
                quote=hollow.group(0).strip()[:120],
                issue="签约主体把履约责任转给第三方或自然人，我方追责时可能只能找到无履约能力的实际施工人。",
                suggestion="要求签约主体（乙方）对全部履约义务承担连带责任，或提供履约保证金/担保。",
            )
        )
    return findings


def find_counterparty_name(flat: str, other_side: str) -> str:
    """从「乙方：XXX」里截出对方主体名，遇到印章/登记信息就截断。

    实测两处坑：①「乙方：某某智能科技有限公司纳税人识别号：91xxxxxxxx…」被整段当成对方名称；
    ②文本已把换行压平，若不在「第一条」「甲方：」处截断，会一路吃到下一段。
    """
    m = re.search(rf"{other_side}(?:（[^）]{{0,20}}）)?\s*[:：]\s*([^\n。；]{{2,40}})", flat)
    if not m:
        return ""
    # 扫描件的印章/签字残渣、紧跟在主体名后面的登记信息、以及下一段开头，都应当在主体名之后截断
    cp = re.split(
        r"(盖章|蓋章|授权代表|授權代表|签字|簽字|日期|第\d+页"
        r"|第[一二三四五六七八九十百零]+条|附[件表录]"
        r"|甲\s*方|乙\s*方|丙\s*方|丁\s*方|买方|賣方|卖方|供方|需方"
        r"|[纳納][税稅]人[识識][别別][号號]|[统統]一[社社]会信用代码|統一社會信用代碼"
        r"|信用代码|信用代碼"
        r"|开户|開戶|账号|帳號|賬號|地址|电话|電話|法定代表人|联系人|聯繫人)",
        m.group(1),
    )[0]
    return f"{other_side}：{cp.strip()[:30]}"


__all__ = ["rule_personal_account", "rule_hollow_subject", "find_counterparty_name"]
