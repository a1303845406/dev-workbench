from __future__ import annotations

import subprocess
import time

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ..core.errors import DomainError
from ..core.model_settings import ModelSettings
from ..core.provider import Provider

router = APIRouter(prefix="/v2/settings/model", tags=["model-settings"])


class ModelConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    name: str = Field(default="", max_length=100)
    base_url: str = Field(default="", max_length=2048)
    model: str = Field(default="", max_length=200)
    key_action: str = "keep"
    api_key: SecretStr = Field(default=SecretStr(""), max_length=8192)
    confirm_external: bool = False

    def settings_data(self) -> dict:
        data = self.model_dump(exclude={"api_key", "confirm_external"})
        data["api_key"] = self.api_key.get_secret_value()
        return data


def local_request(request: Request) -> None:
    # Do not allow another website to mutate settings or trigger credentialed calls.
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise DomainError("SETTINGS_ORIGIN_DENIED", "模型设置仅允许从工作台本身操作。", 403)
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise DomainError("SETTINGS_ORIGIN_DENIED", "模型设置仅允许同源操作。", 403)


@router.get("")
def read_model_settings():
    return ModelSettings().public()


@router.post("")
def save_model_settings(body: ModelConfigIn, request: Request):
    local_request(request)
    return ModelSettings().save(body.settings_data())


@router.post("/reset")
def reset_model_settings(request: Request):
    local_request(request)
    return ModelSettings().reset()


@router.post("/test")
def test_model_settings(body: ModelConfigIn, request: Request):
    local_request(request)
    config = ModelSettings().candidate(body.settings_data())
    if config["mode"] == "deterministic":
        return {"ok": True, "mode": "deterministic", "message": "规则测试模式不调用模型；未进行外部连接测试。", "external_call": False}
    if not body.confirm_external:
        raise DomainError("EXTERNAL_CONFIRM_REQUIRED", "请确认测试将向所选模型发送一条无项目内容的短消息，并可能产生费用。", 422)
    start = time.monotonic()
    try:
        if config["mode"] == "codex":
            content = Provider(config)._codex_complete("Connection test only. Do not use tools.", "Reply OK.", timeout=10)
            if not content.strip():
                raise ValueError()
        else:
            with httpx.Client(timeout=httpx.Timeout(10), follow_redirects=False) as client:
                response = client.post(config["base_url"] + "/chat/completions", headers={"Authorization": "Bearer " + config["api_key"]}, json={"model": config["model"], "messages": [{"role": "user", "content": "Connection test. Reply OK."}], "max_tokens": 16})
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError()
    except (httpx.TimeoutException, subprocess.TimeoutExpired):
        raise DomainError("MODEL_TEST_TIMEOUT", "连接测试超时（10 秒），未保存设置。", 504) from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hint = "鉴权失败，请检查密钥。" if status in (401, 403) else "请求限流或额度不足。" if status == 429 else "请检查基础地址、模型名称及服务状态。"
        raise DomainError("MODEL_TEST_HTTP", f"模型服务返回 HTTP {status}；{hint}未保存设置。", 502) from None
    except Exception:
        raise DomainError("MODEL_TEST_FAILED", "连接失败或响应格式无效，请检查服务地址、模型与本机网络/Codex 安装；未保存设置。", 502) from None
    return {"ok": True, "mode": config["mode"], "external_call": True, "latency_ms": round((time.monotonic() - start) * 1000), "message": "模型已返回有效响应。测试未保存配置，也未发送项目内容。"}
