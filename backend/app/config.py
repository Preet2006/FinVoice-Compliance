"""
Configuration management using pydantic-settings.
Loads all configuration from environment variables with validation.
Fails fast if required variables are missing.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[1] == backend/. Anchoring here (rather
# than env_file=".env") means settings load correctly regardless of the
# process's current working directory — previously, running uvicorn/scripts
# from backend/ silently missed a .env that only existed at the repo root.
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    database_url: str = "sqlite:///./data/compliance.db"

    # CORS
    cors_origin: str = "http://localhost:5173"

    # Audio storage (Phase 5)
    audio_dir: str = str(BACKEND_DIR / "data" / "audio")
    max_upload_size_bytes: int = 100 * 1024 * 1024  # 100 MB, per assignment §4.2

    # GroqCloud API (required for transcription and LLM)
    # Free tier: no credit card required
    # whisper-large-v3-turbo: 20 RPM, 2,000 RPD, 7,200 audio-sec/hr
    # openai/gpt-oss-120b: 30 RPM, 8,000 TPM, 1,000 RPD, 200,000 TPD
    groq_api_key: str = ""

    # Transcription provider (hosted only - no local models)
    transcription_provider: str = "groq"
    groq_transcription_model: str = "whisper-large-v3-turbo"

    # LLM model (sole primary model - no silent substitution)
    groq_llm_model: str = "openai/gpt-oss-120b"

    # Optional: Sarvam AI (manual hosted fallback only)
    sarvam_api_key: str = ""

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Authentication. Local development may use HTTP; production deployments
    # must set AUTH_COOKIE_SECURE=true behind HTTPS.
    auth_cookie_secure: bool = False
    auth_session_hours: int = 12
    auth_login_window_seconds: int = 300
    auth_login_max_attempts: int = 10
    # Only the offline pytest fixture enables this compatibility switch. It is
    # false by default and must never be enabled in a deployed environment.
    allow_legacy_headers: bool = False

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Global settings instance
settings = Settings()
