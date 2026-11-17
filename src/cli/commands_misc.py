"""自检类子命令：doctor。

逐项确认「真实接入」需要的配置是否就位，并把缺什么、去哪儿补直接打出来。
不联网也能跑完前两项，方便新克隆仓库的人先看本地状态。
"""

from __future__ import annotations

from ..config import ROOT, Settings


def cmd_doctor(args) -> int:
    """逐项自检真实接入配置：本地配置、应用凭证、机器人能力、审批接管、用户授权。"""
    from ..feishu_api import FeishuAPI, FeishuError

    settings = Settings.load()
    print("=" * 72)
    print("真实接入配置自检 doctor")
    print("=" * 72)

    print("\n[1/5] 本地配置")
    print(f"  .env 路径：{ROOT / '.env'}")
    if settings.use_llm:
        print(f"  LLM：大模型模式（{settings.llm_base_url} / {settings.llm_model}）")
        if settings.llm_fallback_models:
            print(f"  备用模型：{', '.join(settings.llm_fallback_models)}")
    else:
        print("  LLM：规则模式（未配置 LLM_API_KEY，或 LLM_MODE=mock）—— 规则能抓缺失条款，语义风险要配 key")
    if settings.our_companies:
        print(f"  我方主体：{'、'.join(settings.our_companies)}")
    else:
        print("  [提醒] OUR_COMPANIES 为空：模型不知道「我方是谁」，责任不对等、管辖地这类判断会失准。")
    if settings.our_locations:
        print(f"  我方所在地：{'、'.join(settings.our_locations)}")
    else:
        print("  [提醒] OUR_LOCATIONS 为空：无法判断管辖法院是否对我方有利。")

    print("\n[2/5] 飞书应用凭证")
    if not settings.app_id or not settings.app_secret:
        print("  [X] 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        print("      创建步骤：https://open.feishu.cn/app → 创建企业自建应用")
        print("      → 「凭证与基础信息」复制 App ID / App Secret → 填到 .env")
        return 1
    api = FeishuAPI(settings.app_id, settings.app_secret)
    try:
        token = api.tenant_access_token(force=True)
        print(f"  [OK] 应用凭证有效（token 尾号 {token[-6:]}）")
    except FeishuError as exc:
        print(f"  [X] 凭证校验失败：{exc}")
        return 1

    print("\n[3/5] 机器人能力")
    try:
        info = api.bot_info()
        print(f"  [OK] 应用名称：{info.get('app_name') or '(未返回)'}　启用状态：{info.get('activate_status')}")
    except FeishuError as exc:
        print(f"  [!] 读取机器人信息失败（应用可能还没开启「机器人」能力）：{exc}")

    print("\n[4/5] 审批接管配置")
    if settings.contract_approval_codes:
        for code in settings.contract_approval_codes:
            try:
                approval = api.get_approval(code)
                name = approval.get("approval_name") or "(未返回名称)"
                print(f"  [OK] {name}　approval_code={code}")
            except FeishuError as exc:
                print(f"  [!] 读取审批定义 {code} 失败：{exc}")
        print(f"  自动回写评论：{'开' if settings.contract_comment_enabled else '关'}")
        print("  订阅事件：python -m src.cli approval-subscribe")
    else:
        print("  [ ] CONTRACT_APPROVAL_CODES 为空：机器人不会接管任何审批，只能私聊发合同。")
        print("      拿到审批定义 Code 后填进 .env（用印申请一般在「审批 → 管理后台 → 定义」里）。")

    print("\n[5/5] 用户授权（以本人身份建任务时需要）")
    tokens = {}
    token_file = ROOT / "data" / "user_tokens.json"
    if token_file.exists():
        import json

        try:
            tokens = json.loads(token_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tokens = {}
    if tokens.get("access_token"):
        print("  [OK] 已有用户 token（过期自动刷新）")
    else:
        print("  [ ] 未登录用户账号：需要以本人身份建任务时运行 python -m src.cli login")

    print("\n自检完成。离线试跑：python -m src.cli contract-demo")
    return 0
