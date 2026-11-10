"""All settings come from environment / .env. No secrets in code."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model_small: str = "gemini-2.5-flash-lite"
    gemini_model_large: str = "gemini-2.5-pro"
    gcp_project: str = ""
    gcp_location: str = "us-central1"

    duckdb_path: str = "data/disputedesk.duckdb"

    per_case_budget_usd: float = 0.05
    kill_switch: bool = False


settings = Settings()
