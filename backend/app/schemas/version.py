from pydantic import BaseModel


class VersionResponse(BaseModel):
    backend_version: str
    bot_version: str | None  # None if bot is unreachable
    frontend_version: str | None  # injected build version or local package metadata
    git_sha: str | None
