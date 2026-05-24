"""FastAPI application factory and middleware wiring."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.attack_day import router as attack_day_router
from app.api.auth import router as auth_router
from app.api.autofill import router as autofill_router
from app.api.board import router as board_router
from app.api.buildings import router as buildings_router
from app.api.changelog import router as changelog_router
from app.api.comparison import router as comparison_router
from app.api.config import router as config_router
from app.api.discord_sync import router as discord_sync_router
from app.api.health import router as health_router
from app.api.images import router as images_router
from app.api.lifecycle import router as lifecycle_router
from app.api.members import router as members_router
from app.api.notifications import router as notifications_router
from app.api.post_priority_config import router as post_priority_config_router
from app.api.post_suggestions import router as post_suggestions_router
from app.api.posts import router as posts_router
from app.api.reference import router as reference_router
from app.api.siege_members import router as siege_members_router
from app.api.sieges import router as sieges_router
from app.api.validation import router as validation_router
from app.api.version import router as version_router
from app.config import settings
from app.db.session import engine
from app.dependencies.auth import get_current_user
from app.middleware import RequestLoggingMiddleware
from app.rate_limit import RateLimitExceeded, limiter, rate_limit_exceeded_handler
from app.telemetry import configure_telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — runs startup guards before serving requests."""
    if settings.auth_disabled and settings.environment != "development":
        raise RuntimeError(
            "AUTH_DISABLED=true is not permitted outside development. "
            f"Current environment: {settings.environment}"
        )
    if not settings.auth_disabled:
        if not settings.session_secret or "changeme" in settings.session_secret.lower():
            raise RuntimeError(
                "SESSION_SECRET must be set to a secure random value when auth is enabled. "
                f"Current environment: {settings.environment}"
            )
        if settings.environment != "development" and not settings.bot_service_token:
            raise RuntimeError(
                "BOT_SERVICE_TOKEN must be set in non-development environments. "
                f"Current environment: {settings.environment}"
            )
    # Warn when DAY_ROLE_SYNC_ENABLED=true but role ID env vars are not fully set.
    if settings.day_role_sync_enabled:
        day1 = settings.discord_day_1_role_id
        day2 = settings.discord_day_2_role_id
        if day1 is None and day2 is None:
            logger.warning(
                "DAY_ROLE_SYNC_ENABLED=true but neither DISCORD_DAY_1_ROLE_ID nor "
                "DISCORD_DAY_2_ROLE_ID is set — assign payloads will omit "
                "discord_role_id (v1.0-shape). Set both vars to enable v1.1 payloads."
            )
        elif day1 is None or day2 is None:
            missing = (
                "DISCORD_DAY_1_ROLE_ID" if day1 is None else "DISCORD_DAY_2_ROLE_ID"
            )
            logger.warning(
                "DAY_ROLE_SYNC_ENABLED=true but %s is unset — partial role ID "
                "config is likely a misconfiguration. Assign payloads for the "
                "unconfigured day will omit discord_role_id.",
                missing,
            )
    yield


app = FastAPI(
    title="Siege Assignment API",
    version="0.1.0",
    docs_url="/api/docs" if settings.environment == "development" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# Register rate limiter — must come before routes are added so that the
# limiter state is available on app.state when endpoint decorators fire.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Configure telemetry AFTER the app object is created so that
# FastAPIInstrumentor can wrap it, and after the engine is imported so that
# SQLAlchemyInstrumentor can hook the sync_engine.  OTEL_SERVICE_NAME must be
# set in the container environment (see infra/modules/container-apps.bicep) to
# populate cloud_RoleName in Application Insights.
configure_telemetry(app=app, engine=engine)

app.add_middleware(RequestLoggingMiddleware)

_cors_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
logger.info("CORS allowed origins: %s", _cors_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public routes — no auth required
app.include_router(health_router, prefix="/api")
app.include_router(version_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(auth_router, prefix="/api")

# Protected routes — require authentication
_auth_deps = [Depends(get_current_user)]
app.include_router(reference_router, prefix="/api", dependencies=_auth_deps)
app.include_router(discord_sync_router, prefix="/api", dependencies=_auth_deps)
app.include_router(members_router, prefix="/api", dependencies=_auth_deps)
app.include_router(sieges_router, prefix="/api", dependencies=_auth_deps)
app.include_router(buildings_router, prefix="/api", dependencies=_auth_deps)
app.include_router(siege_members_router, prefix="/api", dependencies=_auth_deps)
app.include_router(board_router, prefix="/api", dependencies=_auth_deps)
app.include_router(lifecycle_router, prefix="/api", dependencies=_auth_deps)
app.include_router(posts_router, prefix="/api", dependencies=_auth_deps)
app.include_router(validation_router, prefix="/api", dependencies=_auth_deps)
app.include_router(autofill_router, prefix="/api", dependencies=_auth_deps)
app.include_router(post_suggestions_router, prefix="/api", dependencies=_auth_deps)
app.include_router(comparison_router, prefix="/api", dependencies=_auth_deps)
app.include_router(attack_day_router, prefix="/api", dependencies=_auth_deps)
app.include_router(changelog_router, prefix="/api", dependencies=_auth_deps)
app.include_router(images_router, prefix="/api", dependencies=_auth_deps)
app.include_router(notifications_router, prefix="/api", dependencies=_auth_deps)
app.include_router(post_priority_config_router, prefix="/api", dependencies=_auth_deps)
