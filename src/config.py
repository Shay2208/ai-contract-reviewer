"""集中配置：全部来自环境变量 / .env 文件，代码里不硬编码密钥。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    """极简 .env 解析，不依赖 python-dotenv。已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in {"1", "true", "yes", "y", "on"}


def _env_list(name: str) -> list[str]:
    raw = _env(name)
    if not raw:
        return []
    return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]


@dataclass
class Settings:
    # --- 飞书应用 ---
    app_id: str = ""
    app_secret: str = ""

    # --- 用户身份授权（OAuth，用于以「你本人」身份建任务）---
    oauth_redirect_uri: str = "http://localhost:8787/callback"
    oauth_scopes: str = "task:task:write task:task:read"

    # --- 大模型（OpenAI 兼容接口）---
    llm_mode: str = "auto"  # auto | mock
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    # 备用模型链：主模型 429/过载/超时/输出异常时按顺序换用（逗号分隔）
    llm_fallback_models: list[str] = field(default_factory=list)
    llm_timeout_review: int = 180  # 合同自由审查输出长，单次调用给足时间

    # --- 存储与输出 ---
    db_path: str = "data/contracts.db"
    contract_out_dir: str = "out/contracts"
    contract_upload_dir: str = "data/uploads"  # 从飞书下载的合同存放目录
    contract_max_mb: int = 20  # 单份合同大小上限

    # --- 审查口径 ---
    our_companies: list[str] = field(default_factory=list)  # OUR_COMPANIES，我方签约主体
    our_locations: list[str] = field(default_factory=list)  # OUR_LOCATIONS，用于判断管辖地是否有利
    contract_penalty_max_pct: float = 30.0  # 违约金占合同额上限（%）

    # --- 机器人接入 ---
    lawyer_open_ids: list[str] = field(default_factory=list)  # LAWYER_OPEN_IDS：法务，用于建任务
    contract_chat_ids: list[str] = field(default_factory=list)  # CONTRACT_CHAT_IDS：允许发合同的群（群聊不 @ 也处理）
    contract_auto_task: bool = False  # 审查完是否自动给法务建任务
    contract_task_due_days: int = 2  # 建法务任务时的默认时限（天）

    # --- 审批流接管 ---
    contract_approval_codes: list[str] = field(default_factory=list)  # 要接管的审批定义 Code（用印申请）
    contract_comment_enabled: bool = True  # 审查完是否自动回写审批评论
    contract_notify_approvers: bool = False  # 是否私聊通知审批人（默认关，避免打扰）
    contract_comment_open_id: str = ""  # 评论署名用哪个用户（飞书不支持应用署名）
    contract_preview_approval_name: str = "用印申请（合同/非合同）"  # 私聊预览时冒充的审批名

    # --- 扫描件 OCR ---
    contract_ocr_enabled: bool = True  # 扫描件 PDF 是否走本地 OCR
    contract_ocr_max_pages: int = 30  # 超过此页数不自动识别（约 10 秒/页）
    contract_ocr_dpi: int = 150  # 渲染分辨率，越高越准也越慢

    @property
    def use_llm(self) -> bool:
        return self.llm_mode != "mock" and bool(self.llm_api_key)

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else ROOT / p

    @property
    def contract_out_path(self) -> Path:
        p = Path(self.contract_out_dir)
        return p if p.is_absolute() else ROOT / p

    @property
    def contract_upload_path(self) -> Path:
        p = Path(self.contract_upload_dir)
        return p if p.is_absolute() else ROOT / p

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> Settings:
        load_env_file(Path(env_file) if env_file else ROOT / ".env")
        return cls(
            app_id=_env("FEISHU_APP_ID"),
            app_secret=_env("FEISHU_APP_SECRET"),
            oauth_redirect_uri=_env("OAUTH_REDIRECT_URI", "http://localhost:8787/callback"),
            oauth_scopes=_env("OAUTH_SCOPES", "task:task:write task:task:read"),
            llm_mode=_env("LLM_MODE", "auto").lower(),
            llm_base_url=_env("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            llm_api_key=_env("LLM_API_KEY"),
            llm_model=_env("LLM_MODEL", "deepseek-chat"),
            llm_fallback_models=[
                m.strip() for m in _env("LLM_FALLBACK_MODELS", "").split(",") if m.strip()
            ],
            llm_timeout_review=int(_env("LLM_TIMEOUT_REVIEW", "180")),
            db_path=_env("DB_PATH", "data/contracts.db"),
            contract_out_dir=_env("CONTRACT_OUT_DIR", "out/contracts"),
            contract_upload_dir=_env("CONTRACT_UPLOAD_DIR", "data/uploads"),
            contract_max_mb=_env_int("CONTRACT_MAX_MB", 20),
            our_companies=_env_list("OUR_COMPANIES"),
            our_locations=_env_list("OUR_LOCATIONS"),
            contract_penalty_max_pct=_env_float("CONTRACT_PENALTY_MAX_PCT", 30.0),
            lawyer_open_ids=_env_list("LAWYER_OPEN_IDS"),
            contract_chat_ids=_env_list("CONTRACT_CHAT_IDS"),
            contract_auto_task=_env_bool("CONTRACT_AUTO_TASK", False),
            contract_task_due_days=_env_int("CONTRACT_TASK_DUE_DAYS", 2),
            contract_approval_codes=_env_list("CONTRACT_APPROVAL_CODES"),
            contract_comment_enabled=_env_bool("CONTRACT_COMMENT_ENABLED", True),
            contract_notify_approvers=_env_bool("CONTRACT_NOTIFY_APPROVERS", False),
            contract_comment_open_id=_env("CONTRACT_COMMENT_OPEN_ID", ""),
            contract_preview_approval_name=_env("CONTRACT_PREVIEW_APPROVAL_NAME", "用印申请（合同/非合同）"),
            contract_ocr_enabled=_env_bool("CONTRACT_OCR_ENABLED", True),
            contract_ocr_max_pages=_env_int("CONTRACT_OCR_MAX_PAGES", 30),
            contract_ocr_dpi=_env_int("CONTRACT_OCR_DPI", 150),
        )
