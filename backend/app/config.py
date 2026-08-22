"""Application settings. Secrets are read from the environment only."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "demo"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"

    database_url: str = "sqlite+pysqlite:///./dybpro.db"
    redis_url: str = "redis://localhost:6379/0"
    celery_task_always_eager: bool = False

    # Devin
    devin_api_key: str | None = None
    devin_org_id: str | None = None
    devin_api_base: str = "https://api.devin.ai"
    devin_api_flavor: str = "v3"
    devin_app_base: str = "https://app.devin.ai"
    devin_orchestrator_acu_limit: int = 8
    devin_child_acu_limit: int = 5
    devin_poll_interval_seconds: float = 10.0
    devin_session_timeout_seconds: float = 1800.0
    devin_request_timeout_seconds: float = 60.0
    devin_child_repo: str | None = None
    agent_max_attempts: int = 2
    allow_local_simulation: bool = True

    # Research daemon
    research_daemon_enabled: bool = True
    research_debounce_seconds: float = 5.0
    research_tick_seconds: float = 15.0
    research_acu_limit: int = 3
    research_max_topics_per_event: int = 4

    # OpenAI
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_vision_model: str = "gpt-4.1-mini"

    # Object storage
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "dybpro"
    s3_region: str = "us-east-1"
    local_artifact_dir: str = "./artifacts"

    # Demo identities / quotas
    seed_admin_api_key: str = "dyb-pro-demo-admin"
    seed_scientist_api_key: str = "dyb-pro-demo-scientist"
    seed_viewer_api_key: str = "dyb-pro-demo-viewer"
    default_acu_quota: int = 200
    default_cycle_quota: int = 25

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def devin_enabled(self) -> bool:
        if not self.devin_api_key:
            return False
        return not (self.devin_api_flavor == "v3" and not self.devin_org_id)

    @property
    def openai_enabled(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
