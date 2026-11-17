"""飞书 AI 合同预审机器人。

模块说明：
- config                配置（环境变量 / .env）
- feishu_api            飞书 REST API 客户端（租户身份）
- feishu_auth           飞书用户授权（OAuth，审批事件订阅用）
- llm                   OpenAI 兼容大模型客户端（带模型降级链、SSL 修复）
- contract.parser       合同解析（docx / 文本型 PDF / 扫描件 OCR）
- contract.checklist    31 项固定清单体检
- contract.reviewer     规则引擎 + 合并去重 + 引用回验 + 定级
- contract.llm_reviewer 提示词与模型调用（自由发散 + 高危清单合并）
- contract.kind         补充/变更类协议识别
- contract.approval     审批接入（读表单 → 下载附件 → 审查 → 写评论）
- contract.bot          长连接机器人（消息事件 + 审批事件）
- contract.render       报告渲染（Markdown / Word）
- contract.store        审查记录存储（SQLite）
- cli                   命令行入口
"""

__version__ = "1.0.0"
__all__ = ["__version__"]

# 可选：长连接 SDK（lark-oapi）放在项目 vendor/ 目录时可离线运行，
# 启动时自动加入 sys.path。公开仓库不带 vendor/，正常 pip 安装即可。
import sys as _sys
from pathlib import Path as _Path

_vendor = _Path(__file__).resolve().parents[1] / "vendor"
if _vendor.is_dir() and str(_vendor) not in _sys.path:
    _sys.path.insert(0, str(_vendor))
