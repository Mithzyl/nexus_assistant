"""Optional Nexus login for explicitly linked accounts; local login remains unchanged."""

from urllib.parse import urlsplit
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field
from assistant_api.core.deps import CurrentUser, DatabaseSession, SettingsDependency, NexusClient
from assistant_api.core.errors import AssistantError
from assistant_api.models import User
from assistant_api.routers.auth import LoginResponse, _issue_session

router = APIRouter(prefix="/api/v1/auth/nexus", tags=["nexus-login"])


def configured_urls(settings):
    """Validate fixed server destinations; never accept callback overrides from a browser."""
    if not settings.nexus_login_enabled:
        raise AssistantError("nexus_login_disabled", "暂未启用 Nexus 登录。", 403)
    for value in (settings.nexus_web_url, settings.nexus_login_callback_url):
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (settings.environment == "production" and parsed.scheme != "https")):
            raise AssistantError("nexus_login_configuration", "Nexus 登录配置不可用。", 503)
    return {"application_id": settings.nexus_application_id,
            "authorization_url": settings.nexus_web_url.rstrip("/") + "/authorize",
            "callback_url": settings.nexus_login_callback_url}


@router.get("/options")
async def options(settings: SettingsDependency, response: Response):
    """Expose safe fixed destinations only; application credentials never leave the service."""
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": False} if not settings.nexus_login_enabled else {
        "enabled": True, **configured_urls(settings)}


class ExchangeRequest(BaseModel):
    """Accept only a short-lived code and its PKCE proof, not a user id or callback."""
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=32, max_length=128)
    code_verifier: str = Field(min_length=43, max_length=128)


async def linked_local_user(db, identity):
    """Resolve the server assertion without email matching, account creation or reactivation."""
    external_id = identity.get("external_user_id")
    if not isinstance(external_id, str) or not external_id or not identity.get("subject"):
        raise AssistantError("nexus_account_not_linked", "请先用原账号登录并关联 Nexus。", 409)
    user = await db.get(User, external_id)
    if user is None or user.status != "active":
        raise AssistantError("nexus_local_account_unavailable", "关联的助手账号不可用，请使用原登录方式。", 403)
    return user


@router.post("/exchange", response_model=LoginResponse)
async def exchange(payload: ExchangeRequest, db: DatabaseSession, settings: SettingsDependency,
                   nexus: NexusClient, response: Response):
    """Consume Nexus approval, then issue an ordinary Assistant session for the mapped user."""
    config = configured_urls(settings)
    identity = await nexus.exchange_login(code=payload.code, redirect_uri=config["callback_url"],
                                         verifier=payload.code_verifier)
    user = await linked_local_user(db, identity)
    session = _issue_session(db, user, settings)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return session


@router.post("/link")
async def link(user: CurrentUser, settings: SettingsDependency, nexus: NexusClient,
               response: Response):
    """Begin explicit linking from the current Assistant session, without merging account data."""
    configured_urls(settings)
    ticket = await nexus.begin_account_link(user.user_id)
    value = ticket.get("ticket")
    if not isinstance(value, str) or not 32 <= len(value) <= 128:
        raise AssistantError("nexus_link_invalid", "关联请求未能完成，请重试。", 502)
    response.headers["Cache-Control"] = "no-store"
    return {"url": settings.nexus_web_url.rstrip("/") + "/account-links#ticket=" + value}
