import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
from apscheduler.triggers.cron import CronTrigger  # type: ignore
import time

from app.services.bbb_service import BBBService

from app.controllers.auth_controller import router as auth_router
from app.controllers.bbb_controller import router as bbb_router
from app.controllers.broadcaster_controller import router as broadcaster_router
from app.controllers.user_controller import router as user_router
from app.controllers.rtmp_controller import router as stream_router
from app.controllers.channels_controller import router as channels_router
from app.controllers.event_controller import router as event_router
from app.controllers.health_controller import router as health_router
from app.controllers.twitch_controller import router as twitch_router
from app.controllers.youtube_controller import router as youtube_router

from app.config.chat_manager import chat_manager
from app.config.twitch_irc import TwitchIRCClient
from app.config.logger_config import get_logger
from app.config.settings import get_settings
from app.config.redis_config import cache

logger = get_logger("Main")
setting = get_settings()
scheduler = AsyncIOScheduler()
bbb_service = BBBService()
twitch_client = TwitchIRCClient()


# Add request logging middleware
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for the FastAPI application
    """
    logger.info("=== APPLICATION STARTUP ===")

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

    # Startup: schedule the IRC client
    twitch_tasks = asyncio.gather(
        twitch_client.connect(),
        # twitch_client.start_token_refresh_scheduler(),
        return_exceptions=True,
    )

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
    scheduler.start()
    logger.info("[Scheduler] BBB meeting cleanup job scheduled")

    logger.info("=== APPLICATION STARTUP COMPLETE ===")

    yield  # App is running

    logger.info("=== APPLICATION SHUTDOWN ===")
    await cache.close()
    logger.info("[cache] Redis cache connection closed")

    # Shutdown: cancel the IRC task
    twitch_tasks.cancel()
    try:
        await twitch_tasks
    except asyncio.CancelledError:
        logger.info("[TwitchIRC] Connect task cancelled cleanly")

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


origins = [
    "http://localhost:3000",  # Frontend URL in development
    "http://127.0.0.1:3000",  # Alternative localhost
    "http://localhost:8000",  # Backend self
    "http://127.0.0.1:8000",  # Backend self alternative
    "http://spoutbreeze-frontend.spoutbreeze.svc.cluster.local:3000",  # Frontend URL in Kubernetes
    "https://frontend.67.222.155.30.nip.io:30443",  # Frontend URL
    "https://frontend.67.222.155.30.nip.io",  # Frontend URL without port
    "https://test.b3.craftschoolship.com",  # BBB URL
    "https://67.222.155.30:8443",  # Keycloak URL
    "https://backend.67.222.155.30.nip.io:30444",  # Backend URL
    "https://backend.67.222.155.30.nip.io",  # Backend URL without port
    "http://localhost:8800",  # Chat Gateway self
]

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "Accept",
        "Origin",
        "Access-Control-Request-Method",
        "Access-Control-Request-Headers",
        # WebSocket specific headers
        "Upgrade",
        "Connection",
        "Sec-WebSocket-Key",
        "Sec-WebSocket-Protocol",
        "Sec-WebSocket-Version",
        # Any custom headers your app uses
        "Accept-Language",
        "Cache-Control",
        "Content-Language",
        "DNT",
        "If-Modified-Since",
        "Keep-Alive",
        "Pragma",
        "Referer",
        "User-Agent",
        "X-CSRFToken",
        "X-Forwarded-For",
        "X-Forwarded-Proto",
        "ngrok-skip-browser-warning",
    ],
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


# Include routers
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(twitch_router, prefix="/api")
app.include_router(youtube_router, prefix="/api")
app.include_router(user_router)
app.include_router(channels_router)
app.include_router(event_router)
app.include_router(stream_router)
app.include_router(broadcaster_router)
app.include_router(bbb_router)


@app.websocket("/ws/chat/")
async def chat_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for chat messages
    """
    logger.info("WebSocket connection requested")
    await chat_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data.startswith("/twitch"):
                message = data[len("/twitch ") :]
                await twitch_client.send_message(message)
                logger.info(f"[TwitchIRC] Sending message: {message}")
            else:
                await chat_manager.broadcast(data)
    except WebSocketDisconnect:
        chat_manager.disconnect(websocket)
        logger.info("[Chat] Client disconnected")
