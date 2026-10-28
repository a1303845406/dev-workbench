"""Local model settings. No API ever serializes a cleartext credential."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .db import data_root
from .errors import DomainError

_LOCK = threading.RLock()
MODES = {"real", "codex", "deterministic"}


def protect_secret(value: str, decrypt: bool = False) -> str:
    if os.name != "nt":
        raise DomainError("SECURE_STORAGE_UNAVAILABLE", "此系统不支持安全密钥持久化，请使用环境变量提供密钥。", 422)
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    raw = base64.b64decode(value) if decrypt else value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    # Explicit signatures matter on 64-bit Windows (LocalFree takes a pointer).
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), "DevWorkbench model key", None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise DomainError("SECRET_STORAGE_ERROR", "无法保护或读取密钥，请在当前 Windows 用户下重新配置。", 422)
    try:
        result = ctypes.string_at(target.data, target.size)
        return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class ModelSettings:
    def __init__(self, root: Path | None = None):
        self.path = (root or data_root()) / "private" / "model-service.json"

    def _load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("mode") not in MODES:
                raise ValueError()
            return data
        except Exception:
            raise DomainError("MODEL_CONFIG_INVALID", "模型配置文件无法读取，请恢复备份或移除该配置后重新设置。", 503) from None

    def effective(self) -> dict:
        stored = self._load()
        if stored is not None:
            try:
                key = os.environ.get("DEVWB_PROVIDER_API_KEY", "") if stored.get("key_source") == "environment" else protect_secret(stored["encrypted_key"], decrypt=True) if stored.get("encrypted_key") else ""
            except Exception:
                raise DomainError("SECRET_STORAGE_ERROR", "保存的密钥无法读取，请使用原 Windows 用户或重新配置。", 503) from None
            return {"mode": stored["mode"], "name": stored.get("name", ""), "base_url": stored.get("base_url", ""), "model": stored.get("model", ""), "api_key": key, "source": "saved"}
        base = os.environ.get("DEVWB_PROVIDER_BASE_URL", "").rstrip("/")
        key = os.environ.get("DEVWB_PROVIDER_API_KEY", "")
        model = os.environ.get("DEVWB_PROVIDER_MODEL", "")
        codex = os.environ.get("DEVWB_CODEX_ENABLED", "").lower() in {"1", "true", "yes"}
        mode = "codex" if codex and model else "real" if base and key and model else "deterministic"
        return {"mode": mode, "name": "环境变量" if mode != "deterministic" else "规则测试", "base_url": base, "model": model, "api_key": key, "source": "environment" if base or key or model or codex else "default"}

    def public(self) -> dict:
        value = self.effective()
        value["has_key"] = bool(value.pop("api_key"))
        value["secure_storage"] = "windows-dpapi" if os.name == "nt" else "environment-only"
        value["priority"] = "已保存设置优先于环境变量；恢复环境配置后使用环境变量。"
        return value

    def candidate(self, data: dict) -> dict:
        current = self.effective()
        mode = data.get("mode")
        if mode not in MODES:
            raise DomainError("INVALID_MODEL_MODE", "请选择真实 API、Codex 或规则测试模式。", 422)
        action = data.get("key_action", "keep")
        key = current["api_key"] if action == "keep" else data.get("api_key", "") if action == "replace" else ""
        value = {"mode": mode, "name": data.get("name", "").strip(), "base_url": data.get("base_url", "").strip().rstrip("/"), "model": data.get("model", "").strip(), "api_key": key, "source": "saved"}
        if action not in {"keep", "replace", "clear"}:
            raise DomainError("INVALID_KEY_ACTION", "密钥操作无效。", 422)
        if action == "replace" and (not key or not key.strip()):
            raise DomainError("MODEL_KEY_REQUIRED", "替换密钥时不能为空。", 422)
        if value["base_url"]:
            try:
                url = urlsplit(value["base_url"])
                invalid = url.scheme not in {"http", "https"} or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment or any(c.isspace() or ord(c) < 32 for c in value["base_url"])
                _ = url.port
            except ValueError:
                invalid = True
            if invalid:
                raise DomainError("INVALID_MODEL_URL", "API 地址须为 http(s) 基础地址，不得包含凭据、查询参数或片段。", 422)
            if url.path.rstrip("/").endswith("/chat/completions"):
                raise DomainError("INVALID_MODEL_URL", "请填写基础地址，不要包含 /chat/completions。", 422)
        if mode == "real" and not all(value[k] for k in ("base_url", "model", "api_key")):
            raise DomainError("MODEL_CONFIG_REQUIRED", "真实 API 模式需要基础地址、模型名称和密钥。", 422)
        if mode == "codex" and not value["model"]:
            raise DomainError("MODEL_CONFIG_REQUIRED", "Codex 模式需要模型名称。", 422)
        stored = self._load()
        value["key_from_environment"] = action == "keep" and (current["source"] == "environment" or bool(stored and stored.get("key_source") == "environment"))
        return value

    def save(self, data: dict) -> dict:
        with _LOCK:
            value = self.candidate(data)
            key = value.pop("api_key")
            value.pop("source")
            from_environment = value.pop("key_from_environment")
            value["key_source"] = "environment" if from_environment else "saved"
            value["encrypted_key"] = protect_secret(key) if key and not from_environment else ""
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = None
            try:
                fd, temp_path = tempfile.mkstemp(prefix=".model-", dir=self.path.parent)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(value, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, self.path)
            except Exception:
                raise DomainError("MODEL_CONFIG_WRITE_FAILED", "模型配置保存失败，请检查数据目录权限。", 500) from None
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
            return self.public()

    def reset(self) -> dict:
        with _LOCK:
            self.path.unlink(missing_ok=True)
            return self.public()
