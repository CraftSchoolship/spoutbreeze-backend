import time
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
from apscheduler.triggers.cron import CronTrigger  # type: ignore
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi

from app.config.database.session import SessionLocal
from app.config.logger_config import get_logger
from app.config.redis_config import cache
from app.config.settings import get_settings
from app.controllers.auth_controller import router as auth_router
from app.controllers.bbb_controller import router as bbb_router
from app.controllers.broadcaster_controller import router as broadcaster_router
from app.controllers.channels_controller import router as channels_router
from app.controllers.event_controller import router as event_router
from app.controllers.facebook_controller import router as facebook_router
from app.controllers.facebook_stream_controller import router as facebook_stream_router
from app.controllers.health_controller import router as health_router
from app.controllers.internal_controller import router as internal_router
from app.controllers.notification_controller import router as notification_router
from app.controllers.payment_controller import router as payment_router
from app.controllers.rtmp_controller import router as stream_router
from app.controllers.twitch_controller import router as twitch_router
from app.controllers.user_controller import router as user_router
from app.controllers.youtube_controller import router as youtube_router

# Import models to ensure they are registered with SQLAlchemy
from app.models import connection_model, fcm_token_model, notification_models, payment_models, user_models  # noqa: F401
from app.services.bbb_service import BBBService
from app.services.event_reminder_service import EventReminderService
from app.services.stream_cleanup_service import StreamCleanupService
from app.services.token_refresh_service import TokenRefreshService

logger = get_logger("Main")
setting = get_settings()
scheduler = AsyncIOScheduler()
bbb_service = BBBService()
# twitch_client = TwitchIRCClient()


# Add request logging middleware
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for the FastAPI application
    """
    logger.info("=== APPLICATION STARTUP ===")

    # Patch OAuth2 scheme with real Keycloak URLs (now that Keycloak should be reachable)
    try:
        from app.controllers.auth_controller import _get_well_known, oauth2_scheme

        wk = _get_well_known()
        oauth2_scheme.model.flows.authorizationCode.authorizationUrl = wk["authorization_endpoint"]  # type: ignore[union-attr, attr-defined]
        oauth2_scheme.model.flows.authorizationCode.tokenUrl = wk["token_endpoint"]  # type: ignore[union-attr, attr-defined]
        logger.info("[Auth] Keycloak well-known URLs loaded for OpenAPI docs")
    except Exception as e:
        logger.warning(f"[Auth] Could not load Keycloak well-known URLs: {e}")

    # Startup: Configure OpenAPI schema
    openapi_schema = get_openapi(
        title="SpoutBreeze API",
        version="1.0.0",
        description="SpoutBreeze API documentation",
        routes=app.routes,
    )

    # Add components if they don't exist
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}

    if "schemas" not in openapi_schema["components"]:
        openapi_schema["components"]["schemas"] = {}

    # Add security schemes
    openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    }

    # Apply security globally
    openapi_schema["security"] = [{"bearerAuth": []}]

    # Set the schema
    app.openapi_schema = openapi_schema

    # Initialize Redis cache
    await cache.connect()
    logger.info("[cache] Redis cache connected")

    logger.info("[TwitchIRC] Background connect and token refresh tasks scheduled")

    # Set up scheduler for bbb meeting cleanup
    scheduler.add_job(
        bbb_service._clean_up_meetings_background,
        trigger=CronTrigger(hour="3", minute="0"),  # Every day at 3 AM
        id="bbb_meeting_cleanup_job",
        name="BBB Meeting Cleanup Job",
        replace_existing=True,
        misfire_grace_time=3600,  # 1 hour
        kwargs={"days": 30},
    )
    logger.info("[Scheduler] BBB meeting cleanup job scheduled")

    # Set up scheduler for stream cleanup (every 5 minutes)
    async def _stream_cleanup_job():
        async with SessionLocal() as db:
            await StreamCleanupService.cleanup_stale_streams(db)

    scheduler.add_job(
        _stream_cleanup_job,
        trigger=IntervalTrigger(minutes=5),
        id="stream_cleanup_job",
        name="Stream Cleanup Job",
        replace_existing=True,
        misfire_grace_time=60,
    )
    logger.info("[Scheduler] Stream cleanup job scheduled (every 5 min)")

    # Set up scheduler for token refresh (every 30 minutes)
    async def _token_refresh_job():
        async with SessionLocal() as db:
            await TokenRefreshService.refresh_expiring_tokens(db)

    scheduler.add_job(
        _token_refresh_job,
        trigger=IntervalTrigger(minutes=30),
        id="token_refresh_job",
        name="Token Refresh Job",
        replace_existing=True,
        misfire_grace_time=600,  # 10 min grace
    )
    logger.info("[Scheduler] Token refresh job scheduled (every 30 min)")

    # Set up scheduler for event reminders (every 15 minutes)
    async def _event_reminder_job():
        async with SessionLocal() as db:
            await EventReminderService.send_due_reminders(db)

    scheduler.add_job(
        _event_reminder_job,
        trigger=IntervalTrigger(minutes=15),
        id="event_reminder_job",
        name="Event Reminder Job",
        replace_existing=True,
        misfire_grace_time=300,  # 5 min grace
    )
    logger.info("[Scheduler] Event reminder job scheduled (every 15 min)")

    scheduler.start()

    logger.info("=== APPLICATION STARTUP COMPLETE ===")

    yield  # App is running

    logger.info("=== APPLICATION SHUTDOWN ===")
    scheduler.shutdown(wait=False)
    logger.info("[Scheduler] Shut down")
    await cache.close()
    logger.info("[cache] Redis cache connection closed")

    logger.info("=== APPLICATION SHUTDOWN COMPLETE ===")


app = FastAPI(
    title="SpoutBreeze API",
    version="1.0.0",
    description="SpoutBreeze API documentation",
    lifespan=lifespan,
)


# Add request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    logger.info(f"Incoming request: {request.method} {request.url}")
    # logger.info(f"Headers: {dict(request.headers)}")

    response = await call_next(request)

    process_time = time.time() - start_time
    logger.info(
        f"Request completed: {request.method} {request.url} - Status: {response.status_code} - Time: {process_time:.4f}s"
    )

    return response


# Override the default Swagger UI to add OAuth support
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    logger.info("Swagger UI requested")
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
        swagger_favicon_url="/favicon.ico",
        init_oauth={
            "clientId": setting.keycloak_client_id,
            "usePkceWithAuthorizationCodeGrant": True,
            "clientSecret": setting.keycloak_client_secret,
            "realm": setting.keycloak_realm,
            "appName": "SpoutBreeze API",
            "scope": "openid profile email",
            "additionalQueryStringParams": {},
        },
    )


# Parse CORS origins from settings (comma-separated string)
origins = [origin.strip() for origin in setting.cors_origins.split(",") if origin.strip()]

# Configure CORS with both explicit origins and regex pattern support
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Explicit origins (frontend, API, etc.)
    allow_origin_regex=setting.cors_origin_regex
    if setting.cors_origin_regex
    else None,  # Regex for dynamic origins (BBB instances)
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],  # Allow all headers for flexibility
)


@app.get("/", tags=["Root"])
async def root():
    """
    Root endpoint that returns a welcome message
    """
    logger.info("Root endpoint accessed")
    return {"message": "Welcome to SpoutBreeze API", "timestamp": time.time()}


# Add a simple test endpoint
@app.get("/api/test", tags=["Test"])
async def test_endpoint():
    """Test endpoint to verify API is working"""
    logger.info("Test endpoint accessed")
    return {
        "status": "success",
        "message": "API is working correctly",
        "timestamp": time.time(),
    }


# Register routers
app.include_router(internal_router)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(twitch_router, prefix="/api")
app.include_router(youtube_router, prefix="/api")
app.include_router(facebook_router, prefix="/api")
app.include_router(user_router)
app.include_router(channels_router)
app.include_router(event_router)
app.include_router(stream_router)
app.include_router(broadcaster_router)
app.include_router(bbb_router)
app.include_router(facebook_stream_router)
app.include_router(payment_router)
app.include_router(notification_router)
