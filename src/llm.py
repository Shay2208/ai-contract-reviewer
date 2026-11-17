"""OpenAI 兼容大模型客户端（只用标准库 urllib，不引入额外依赖）。"""

from __future__ import annotations

import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)


def _ssl_context() -> ssl.SSLContext:
    """HTTPS 证书上下文：优先用 certifi 的 CA 包（vendor 自带），失败退回系统默认。"""
    try:
        import certifi  # noqa: F401  vendor 里有 certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


class LLMError(RuntimeError):
    pass


_SATURATED_HINTS = (
    "429",
    "1302",
    "1305",
    "访问量过大",
    "速率限制",
    "rate limit",
    "500",
    "502",
    "503",
    "504",
    "timed out",
    "超时",
)
_FATAL_HINTS = ("401", "403", "api key", "invalid api", "未配置", "insufficient")


def _is_saturated(exc: Exception) -> bool:
    """限流/过载/超时这类「换个模型可能就好了」的失败。"""
    text = str(exc).lower()
    if any(h in text for h in _FATAL_HINTS):
        return False
    return any(h in text for h in _SATURATED_HINTS)


def _is_fatal(exc: Exception) -> bool:
    """认证/余额/配置类失败：换模型也没用，直接抛。"""
    text = str(exc).lower()
    return any(h in text for h in _FATAL_HINTS)


def _extract_json(text: str) -> Any:
    """从模型输出里抠出 JSON，容忍 ```json 代码块和前后废话。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            snippet = text[start : end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                continue
    raise LLMError(f"模型返回的不是合法 JSON: {text[:200]}")


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 60,
        temperature: float = 0.1,
        fallback_models: list[str] | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or "deepseek-chat"
        self.timeout = timeout
        self.temperature = temperature
        self.fallback_models = list(fallback_models or [])

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url)

    @classmethod
    def from_settings(cls, settings) -> LLMClient:
        """按 .env 配置构造；未配置 key 时 available=False。"""
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            fallback_models=list(getattr(settings, "llm_fallback_models", []) or []),
        )

    def chat_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 2000,
        retries: int = 3,
        timeout: int | None = None,
    ) -> Any:
        if not self.available:
            raise LLMError("未配置 LLM_API_KEY，无法调用大模型")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        models = [self.model] + [m for m in self.fallback_models if m and m != self.model]
        last_error: Exception | None = None
        for index, model in enumerate(models):
            payload["model"] = model
            # GLM 4.5+ 默认开思考模式，会把 token 花在推理上导致 content 为空，明确关掉
            if re.match(r"glm-4\.[5-9]", model or ""):
                payload["thinking"] = {"type": "disabled"}
            else:
                payload.pop("thinking", None)
            try:
                # 还有备用模型时，429 不在这里耗退避：立刻换模型更快出结果
                return self._chat_with_retry(
                    payload, retries, timeout, fast_fail_429=index + 1 < len(models)
                )
            except LLMError as exc:
                last_error = exc
                if index + 1 < len(models) and not _is_fatal(exc):
                    log.warning("模型 %s 不可用（%s），改用 %s", model, str(exc)[:120], models[index + 1])
                    continue
                raise
        raise LLMError(f"调用大模型失败（已尝试 {', '.join(models)}）: {last_error}")

    def _chat_with_retry(
        self,
        payload: dict[str, Any],
        retries: int,
        timeout: int | None,
        fast_fail_429: bool = False,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                import time

                if attempt:
                    time.sleep(3 * attempt)  # 429/限流退避
                result = self._post(payload, timeout=timeout)
                if result is None or result == "":
                    raise ValueError("模型返回空内容")
                return result
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                last_error = LLMError(f"HTTP {exc.code}: {body[:300]}")
                # 有些兼容接口不支持 response_format，去掉后重试一次
                if "response_format" in payload and exc.code in (400, 422):
                    payload.pop("response_format", None)
                    continue
                if exc.code == 429:
                    if fast_fail_429:
                        raise last_error from exc  # 交给上层换备用模型，别在这里等
                    # 限流（智谱 1302「已达到速率限制」）：退避要够长，否则重试也白搭
                    import time

                    wait = 0.0
                    try:
                        wait = float(exc.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        wait = 0.0
                    time.sleep(max(wait, 15.0 * max(attempt, 1)))
                    continue
                if exc.code in (500, 502, 503, 504):
                    continue
                raise last_error from exc
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                # 空内容常见于 response_format 不兼容：去掉后重试
                if "response_format" in payload and "空内容" in str(exc):
                    payload.pop("response_format", None)
                continue
        raise LLMError(f"调用大模型失败: {last_error}")

    def _post(self, payload: dict[str, Any], timeout: int | None = None) -> Any:
        url = f"{self.base_url}/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(
            request, timeout=timeout or self.timeout, context=_ssl_context()
        ) as response:
            body = response.read().decode("utf-8")
        parsed = json.loads(body)
        content = parsed["choices"][0]["message"]["content"]
        return _extract_json(content)
