"""
Silicon to Software (S2S) - Central Configuration
All settings loaded from environment variables with sensible defaults.
Compatible with Python 3.10+ (no pydantic-settings dependency).
"""

import os
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

# Load .env file (does NOT override already-set env vars — intentional)
load_dotenv(Path(__file__).parent / ".env")

# Extend NO_PROXY from .env, merging with any system-level NO_PROXY.
# This is needed because load_dotenv won't override system env vars, but the
# Cowork sandbox sets NO_PROXY to only local ranges while our .env adds LLM domains.
_dotenv_raw = dotenv_values(Path(__file__).parent / ".env")
_dotenv_no_proxy = _dotenv_raw.get("NO_PROXY", "")
if _dotenv_no_proxy:
    _sys_no_proxy = os.environ.get("NO_PROXY", "") or os.environ.get("no_proxy", "")
    _existing = {d.strip() for d in _sys_no_proxy.split(",") if d.strip()}
    _extra    = {d.strip() for d in _dotenv_no_proxy.split(",") if d.strip()}
    _merged   = ",".join(sorted(_existing | _extra))
    os.environ["NO_PROXY"] = _merged
    os.environ["no_proxy"] = _merged


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, str(default)).lower()
    return val in ("1", "true", "yes", "on")


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


class Settings:
    """Application settings loaded from .env file.

    Reads environment variables at instantiation time so tests can
    modify os.environ before creating a new Settings() instance.
    """

    def __init__(self):
        # --- LLM API Keys ---
        self.anthropic_api_key = _env("ANTHROPIC_API_KEY", "")
        self.openai_api_key = _env("OPENAI_API_KEY", "")
        self.glm_api_key = _env("GLM_API_KEY", "")
        self.deepseek_api_key = _env("DEEPSEEK_API_KEY", "")

        # --- DeepSeek ---
        self.deepseek_base_url = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        # deepseek-chat = DeepSeek-V3 (general), deepseek-reasoner = DeepSeek-R1 (reasoning)
        self.deepseek_model = _env("DEEPSEEK_MODEL", "deepseek-chat")
        self.deepseek_fast_model = _env("DEEPSEEK_FAST_MODEL", "deepseek-chat")

        # --- LLM Models ---
        # Priority (auto-detected from available API keys):
        #   GLM via Z.AI  → primary when GLM_API_KEY is set (cheapest, Anthropic-compatible API)
        #   DeepSeek-V3   → ONLY when GLM is NOT set, or when the user
        #                    explicitly opts in via INCLUDE_DEEPSEEK_FALLBACK=true
        #   Ollama local  → ONLY when no cloud key is set (true air-gap mode), or
        #                    when the user explicitly opts in via INCLUDE_OLLAMA_FALLBACK=true
        # Override any of these via PRIMARY_MODEL / FAST_MODEL env vars in .env.
        #
        # IMPORTANT (2026-04-25 #1 — fix for "Ollama 404 → P4 phase failed"):
        # Ollama is NOT included in the fallback chain by default when a cloud
        # LLM key is configured. Previously last_resort_model was always
        # `ollama/qwen2.5-coder:32b`, which produced a misleading
        # "All models in fallback chain failed. Last error: Client error
        # '404 Not Found' for url 'http://localhost:11434/api/chat'"
        # whenever Ollama was either offline or didn't have that specific
        # model installed — even though the cloud LLMs were working fine on
        # subsequent retries.
        #
        # IMPORTANT (2026-04-25 #2 — user request "dont use deepseek api use
        # only glm"): DeepSeek is NOT included in the fallback chain by
        # default when GLM is set. Same root cause as the Ollama fix: the
        # user's DEEPSEEK_API_KEY may be exhausted ("Insufficient Balance"
        # 402 error from project hxhc P7) and using it as a fallback just
        # masks transient GLM errors with a permanent DeepSeek failure.
        # User can opt back in with INCLUDE_DEEPSEEK_FALLBACK=true.
        _has_glm      = bool(_env("GLM_API_KEY", ""))
        _has_deepseek = bool(_env("DEEPSEEK_API_KEY", ""))
        _has_anthropic = bool(_env("ANTHROPIC_API_KEY", ""))
        _has_any_cloud = _has_glm or _has_deepseek or _has_anthropic

        _include_deepseek = (
            _env_bool("INCLUDE_DEEPSEEK_FALLBACK", False)
            or (_has_deepseek and not _has_glm and not _has_anthropic)
        )
        _deepseek_default = "deepseek-chat" if _include_deepseek else ""

        _include_ollama = (
            _env_bool("INCLUDE_OLLAMA_FALLBACK", False)
            or not _has_any_cloud  # auto-include only in pure air-gap mode
        )
        _ollama_default = "ollama/qwen2.5-coder:32b" if _include_ollama else ""

        self.primary_model = _env("PRIMARY_MODEL",
            "glm-4.7"      if _has_glm      else
            "deepseek-chat" if _has_deepseek else
            "ollama/qwen2.5-coder:32b")
        self.fast_model = _env("FAST_MODEL",
            "glm-4.5-air"  if _has_glm      else
            "deepseek-chat" if _has_deepseek else
            "ollama/qwen2.5-coder:32b")
        self.fallback_model = _env("FALLBACK_MODEL",
            _deepseek_default if (_has_glm and _include_deepseek) else
            _ollama_default)
        self.last_resort_model = _env("LAST_RESORT_MODEL", _ollama_default)

        # --- Ollama (Air-Gap) ---
        self.ollama_base_url = _env("OLLAMA_BASE_URL", "http://localhost:11434")
        self.ollama_model = _env("OLLAMA_MODEL", "qwen2.5-coder:32b")

        # --- GLM / Z.AI ---
        self.glm_base_url = _env("GLM_BASE_URL", "https://api.z.ai/api/anthropic")
        self.glm_model = _env("GLM_MODEL", "glm-4.7")
        self.glm_fast_model = _env("GLM_FAST_MODEL", "glm-4.5-air")

        # --- GitHub / Git Integration ---
        self.github_token = _env("GITHUB_TOKEN", "")
        self.github_repo = _env("GITHUB_REPO", "")          # e.g. "owner/hardware-pipeline-demo"
        self.github_repo_url = _env("GITHUB_REPO_URL", "")  # HTTPS clone URL (auto-derived if empty)
        self.git_enabled = _env_bool("GIT_ENABLED", bool(_env("GITHUB_TOKEN", "")))
        # P26 #19 (2026-04-26): default ON because Windows / macOS Git
        # Credential Manager (GCM) otherwise intercepts every push and
        # pops the git-ecosystem OAuth dialog — even when the remote
        # URL has the PAT embedded. Set to false ONLY if you genuinely
        # want GCM to handle auth (rare — it conflicts with the
        # embedded-PAT flow the agent uses).
        self.git_bypass_credential_helper = _env_bool("GIT_BYPASS_CREDENTIAL_HELPER", True)

        # --- Component Search APIs ---
        self.digikey_client_id = _env("DIGIKEY_CLIENT_ID", "")
        self.digikey_client_secret = _env("DIGIKEY_CLIENT_SECRET", "")
        self.digikey_api_url = _env("DIGIKEY_API_URL", "https://api.digikey.com/v3")
        self.mouser_api_key = _env("MOUSER_API_KEY", "")
        self.mouser_api_url = _env("MOUSER_API_URL", "https://api.mouser.com/api/v2")

        # --- Database ---
        self.database_url = _env("DATABASE_URL", "sqlite:///./hardware_pipeline.db")

        # --- ChromaDB ---
        self.chroma_persist_dir = _env("CHROMA_PERSIST_DIR", "./chroma_data")
        self.chroma_collection_name = _env("CHROMA_COLLECTION_NAME", "component_datasheets")
        # Disable ChromaDB telemetry (prevents posthog network calls on startup)
        import os
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
        os.environ.setdefault("CHROMA_ANONYMIZED_TELEMETRY", "False")

        # --- Embedding ---
        self.embedding_model = _env("EMBEDDING_MODEL", "text-embedding-3-large")
        self.offline_embedding_model = _env("OFFLINE_EMBEDDING_MODEL", "nomic-embed-text")

        # --- Application ---
        self.app_name = _env("APP_NAME", "Silicon to Software (S2S)")
        self.app_env = _env("APP_ENV", "development")
        self.debug = _env_bool("DEBUG", True)
        self.log_level = _env("LOG_LEVEL", "INFO")

        # --- Password Gate ---
        # Set to enable a login page protecting the entire app.
        # Leave empty to disable (useful for local dev and on-prem).
        self.app_password = _env("APP_PASSWORD", "")

        # --- Server ---
        self.fastapi_host = _env("FASTAPI_HOST", "0.0.0.0")
        self.fastapi_port = _env_int("FASTAPI_PORT", 8000)
        self.streamlit_port = _env_int("STREAMLIT_PORT", 8501)

        # --- Paths ---
        self.base_dir = Path(__file__).parent
        self.output_dir = Path(__file__).parent / "output"
        self.templates_dir = Path(__file__).parent / "templates"
        self.data_dir = Path(__file__).parent / "data"

    @property
    def fallback_chain(self) -> list:
        """Ordered list of models to try, with empty entries removed and
        duplicates collapsed (so a chain like
        [glm-5.1, glm-5.1, deepseek-chat, ""] becomes
        [glm-5.1, deepseek-chat]).

        Empty strings come from `last_resort_model` / `fallback_model`
        being unset when the user has cloud keys but explicitly opted out
        of the Ollama air-gap fallback (default behaviour now — see the
        note in `__init__`)."""
        raw = [
            self.primary_model,
            self.fast_model,
            self.fallback_model,
            self.last_resort_model,
        ]
        seen: set[str] = set()
        chain: list[str] = []
        for m in raw:
            if not m or m in seen:
                continue
            seen.add(m)
            chain.append(m)
        return chain

    @property
    def has_any_llm_key(self) -> bool:
        return bool(self.anthropic_api_key or self.glm_api_key or self.deepseek_api_key)

    @property
    def is_air_gapped(self) -> bool:
        return not self.has_any_llm_key

    @property
    def api_base_url(self) -> str:
        # Use 127.0.0.1 for client connections — 0.0.0.0 is a bind address only
        host = self.fastapi_host if self.fastapi_host not in ("0.0.0.0", "") else "127.0.0.1"
        return f"http://{host}:{self.fastapi_port}"

    def get_api_key_status(self) -> dict:
        return {
            "Anthropic": (bool(self.anthropic_api_key), "✅" if self.anthropic_api_key else "⬜"),
            "DeepSeek": (bool(self.deepseek_api_key), "✅" if self.deepseek_api_key else "⬜"),
            "GLM / Z.AI": (bool(self.glm_api_key), "✅" if self.glm_api_key else "⬜"),
            "OpenAI": (bool(self.openai_api_key), "✅" if self.openai_api_key else "⬜"),
            "DigiKey": (bool(self.digikey_client_id and self.digikey_client_secret), "✅" if self.digikey_client_id else "⬜"),
            "Mouser": (bool(self.mouser_api_key), "✅" if self.mouser_api_key else "⬜"),
        }


# Singleton instance
settings = Settings()
