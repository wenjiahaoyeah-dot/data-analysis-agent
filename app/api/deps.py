"""FastAPI 依赖：轻量身份解析。

不做注册登录，用 X-API-Key 识别调用方：
    带 key  -> user_id = "u_" + sha256(key)[:12]（不存明文）
    不带 key -> anonymous（受 REQUIRE_API_KEY 控制是否放行）
启用 REQUIRE_API_KEY 时还要校验预先配置的 API_KEYS。默认演示模式下，
任意 Key 只是命名空间标识，不提供身份认证。
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import Header, HTTPException

from app.config import settings
from app.repository.base import MetadataRepository
from app.repository.factory import get_repository
from app.repository.models import ANONYMOUS_USER, User


def user_id_from_key(api_key: str | None) -> str:
    if not api_key:
        return ANONYMOUS_USER
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
    return f"u_{digest}"


def get_repo() -> MetadataRepository:
    return get_repository()


def get_current_user(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> User:
    if settings.require_api_key:
        if not settings.api_keys:
            raise HTTPException(status_code=503, detail="服务未配置 API_KEYS")
        supplied = (x_api_key or "").encode("utf-8")
        if not supplied or not any(hmac.compare_digest(supplied, key.encode("utf-8")) for key in settings.api_keys):
            raise HTTPException(status_code=401, detail="缺少或无效的 X-API-Key")

    user_id = user_id_from_key(x_api_key)
    return get_repository().ensure_user(user_id, display_name=user_id)


def ensure_owner(resource_owner: str, current_user: User) -> None:
    """资源不属于当前租户时统一返回 404（不泄露"存在但无权限"）。"""
    if resource_owner != current_user.id:
        raise HTTPException(status_code=404, detail="资源不存在")
