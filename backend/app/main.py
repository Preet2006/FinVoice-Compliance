"""
Main FastAPI application entry point.
Phase 1: Basic health check endpoint with CORS configuration.
"""

import sys

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.routers import auth, calls, corrections, dashboard, media, meta, uploads, violations

# Windows console defaults to cp1252, which can't encode the emoji in the
# startup/shutdown log lines below; force UTF-8 so this runs in any terminal.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Create FastAPI application
app = FastAPI(
    title="Shaha Finlease - Speech Analytics & Compliance API",
    description="RBI-compliant collection call monitoring and compliance detection system",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(meta.router)
app.include_router(auth.router)
app.include_router(calls.router)
app.include_router(corrections.router)
app.include_router(dashboard.router)
app.include_router(uploads.router)
app.include_router(violations.router)
app.include_router(media.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """
    Normalizes FastAPI/Starlette HTTPExceptions to the {error: {code,
    message}} shape from MASTER_IMPLEMENTATION_PLAN §4.4. Routers that
    already raise HTTPException(detail={"error": {...}}) pass through
    unchanged; anything else (e.g. a 404 from a bad path) gets wrapped.
    """
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed.",
                "details": exc.errors(),
            }
        },
    )


@app.get("/api/health")
async def health_check():
    """
    Health check endpoint.
    Returns 200 OK with status message.
    Used to verify the API is running.
    """
    return {"status": "ok"}


@app.get("/")
async def root():
    """
    Root endpoint redirects to API documentation.
    """
    return {
        "message": "Shaha Finlease Speech Analytics & Compliance API",
        "docs": "/api/docs",
        "health": "/api/health",
    }


# Startup event - validates configuration
@app.on_event("startup")
async def startup_event():
    """
    Validates configuration on startup.
    Fails fast if required API keys are missing (when needed in later phases).
    """
    print("🚀 Starting Shaha Finlease API...")
    print(f"📊 Database: {settings.database_url}")
    print(f"🌐 CORS Origin: {settings.cors_origin}")
    
    # Phase 1: Basic validation only
    # Later phases will enforce GROQ_API_KEY requirement
    if settings.groq_api_key:
        print(f"✅ Groq API Key configured")
    else:
        print(f"⚠️  Groq API Key not set (required for Phase 6+)")
    
    print("✅ API server started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    print("👋 Shutting down Shaha Finlease API...")
