"""Same-origin protection for credentialed local HTTP operations."""
from fastapi import Request
from devworkbench.shared.errors import DomainError

def local_request(request: Request) -> None:
    # Do not allow another website to mutate settings or trigger credentialed calls.
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise DomainError("SETTINGS_ORIGIN_DENIED", "模型设置仅允许从工作台本身操作。", 403)
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise DomainError("SETTINGS_ORIGIN_DENIED", "模型设置仅允许同源操作。", 403)


