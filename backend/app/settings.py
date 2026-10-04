"""Runtime settings, read from environment variables (see .env.example)."""
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["local", "together", "off"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("/data")
    config_dir: Path = Path("/app/config/assignments")

    # Together AI (de-identified content only)
    together_api_key: str = ""
    together_base_url: str = "https://api.together.xyz/v1"
    together_model_text: str = ""
    together_model_vision: str = ""

    # Local model server (Ollama / LM Studio, OpenAI-compatible)
    local_llm_base_url: str = "http://host.docker.internal:11434/v1"
    local_llm_model: str = ""
    local_llm_model_vision: str = ""

    # Which provider handles each AI task
    ai_task_deidentify_check: Provider = "local"
    ai_task_reference_extract: Provider = "local"
    ai_task_grade_suggest: Provider = "together"
    ai_task_design_review: Provider = "together"
    ai_task_feedback_write: Provider = "together"

    # Show every outbound payload for approval before it is sent
    require_send_confirmation: bool = True

    # Blind marking: AI mark suggestions stay hidden until the instructor reveals them
    blind_ai_marks: bool = True

    # Reference checking
    crossref_mailto: str = ""

    # Backblaze B2 (backups only)
    b2_key_id: str = ""
    b2_application_key: str = ""
    b2_bucket: str = ""
    b2_endpoint: str = ""

    def together_configured(self) -> bool:
        return bool(self.together_api_key) and not self.together_api_key.startswith("__REPLACE")


settings = Settings()
