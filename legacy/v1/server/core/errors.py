"""Unified API error model (Interface Design §1.1)."""
from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, code: str, status: int, message: str, detail: Any = None):
        self.code = code
        self.status = status
        self.message = message
        self.detail = detail
        super().__init__(message)


def name_conflict(msg: str = "目录已存在", detail: Any = None) -> ApiError:
    return ApiError("PROJECT_NAME_CONFLICT", 409, msg, detail)


def delete_confirm_missing() -> ApiError:
    return ApiError("DELETE_CONFIRM_MISSING", 400, "删除项目须携带 confirm_name 二次确认")


def project_locked(detail: Any = None) -> ApiError:
    return ApiError("PROJECT_LOCKED", 409, "项目正在其他窗口编辑（警示，不阻断）", detail)


def path_not_found(path: str) -> ApiError:
    return ApiError("PATH_NOT_FOUND", 400, "路径不存在或不可读", {"path": path})


def path_out_of_whitelist(path: str, allowed: list[str]) -> ApiError:
    return ApiError("PATH_OUT_OF_WHITELIST", 403, "路径不在允许的扫描边界内",
                    {"path": path, "allowed_roots": allowed})


def scan_limit_exceeded(what: str) -> ApiError:
    return ApiError("SCAN_LIMIT_EXCEEDED", 400, f"扫描超出上限：{what}")


def stage_not_confirmed(detail: Any = None) -> ApiError:
    return ApiError("STAGE_NOT_CONFIRMED", 409, "当前阶段尚未确认，不能推进", detail)


def hard_constraint_violation(missing: list[str]) -> ApiError:
    return ApiError("HARD_CONSTRAINT_VIOLATION", 422,
                    "模块组合违反硬约束：最少须包含 需求→任一开发→测试→验收", {"missing": missing})


def budget_exceeded(detail: Any = None) -> ApiError:
    return ApiError("BUDGET_EXCEEDED_BLOCKED", 422, "输入包压缩后仍超预算，已阻断该环", detail)


def prompt_not_final(prompt_id: str) -> ApiError:
    return ApiError("PROMPT_NOT_FINAL", 409, "提示词未定稿，不可归档导出", {"prompt_id": prompt_id})


def handoff_stale(affected_rings: list[str]) -> ApiError:
    return ApiError("HANDOFF_STALE", 409, "交接摘要已过期（基于旧版提示词）",
                    {"stale_rings": affected_rings, "action_required": "regen-handoff"})


def sanitize_blocked(hits: list[dict]) -> ApiError:
    return ApiError("SANITIZE_BLOCKED", 422, "脱敏扫描命中阻断级规则，已阻断", {"hits": hits})


def outbound_confirm_required() -> ApiError:
    return ApiError("OUTBOUND_CONFIRM_REQUIRED", 428,
                    "模型外发调用缺少知情确认（outbound_confirmation）")


def schema_version_unsupported(path: str, got: int) -> ApiError:
    return ApiError("SCHEMA_VERSION_UNSUPPORTED", 422,
                    "数据 schemaVersion 不兼容，需升级产品或降级数据", {"path": path, "got": got})


def config_invalid(path: str, why: str) -> ApiError:
    return ApiError("CONFIG_INVALID", 422, "配置损坏，已回退最近完好副本", {"path": path, "why": why})


def export_adapter_failed(format: str, why: str) -> ApiError:
    return ApiError("EXPORT_ADAPTER_FAILED", 502, "适配导出失败，可降级通用 Markdown",
                    {"format": format, "why": why})
