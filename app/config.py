"""Cấu hình đọc từ biến môi trường."""
import os


def _bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self.secret_key = os.getenv("SECRET_KEY", "")
        self.data_dir = os.getenv("DATA_DIR", "./data")
        self.admin_username = os.getenv("ADMIN_USERNAME", "admin")
        self.admin_password = os.getenv("ADMIN_PASSWORD", "")
        self.cookie_secure = _bool("COOKIE_SECURE", "1")
        self.user_session_days = int(os.getenv("USER_SESSION_DAYS", "30"))
        self.admin_session_hours = int(os.getenv("ADMIN_SESSION_HOURS", "12"))

        # Claude API
        self.llm_enabled = _bool("LLM_ENABLED", "1")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.anthropic_base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        self.llm_model = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
        self.llm_fast_model = os.getenv("LLM_FAST_MODEL", "claude-haiku-5-5")
        self.llm_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "1200"))

        # Embedding (tùy chọn, API kiểu OpenAI /v1/embeddings, ví dụ Voyage AI hoặc Ollama)
        self.embed_base_url = os.getenv("EMBED_BASE_URL", "").rstrip("/")
        self.embed_api_key = os.getenv("EMBED_API_KEY", "")
        self.embed_model = os.getenv("EMBED_MODEL", "")

        self.max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "25"))

    @property
    def llm_ready(self) -> bool:
        return self.llm_enabled and bool(self.anthropic_api_key)

    @property
    def embed_ready(self) -> bool:
        return bool(self.embed_base_url and self.embed_model)

    def validate(self) -> None:
        if len(self.secret_key) < 32:
            raise RuntimeError("SECRET_KEY phải dài ít nhất 32 ký tự. Tạo bằng: python -c \"import secrets;print(secrets.token_hex(32))\"")


settings = Settings()
