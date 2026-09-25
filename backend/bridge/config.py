from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Found regardless of the folder uvicorn is started from; backend/.env wins over a
# project-root .env when both exist.
ENV_FILES = (BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    assistant_name: str = "J.A.R.V.I.G."
    system_prompt: str = (
        "You are J.A.R.V.I.G., a calm, precise, slightly witty personal assistant. "
        "Keep spoken answers short unless asked for detail. Incident analysis (sorting the "
        "user's Gmail alert emails) is switched on and off by the system when the user says "
        "\"enable incident analysis\" or \"turn off incident analysis\"; tell them that if they ask."
        " You have no tools: you can't read files, browse the web, send email or run code."
    )

    agent_provider: str = "echo"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    claude_code_cli: str = "claude"
    claude_code_model: str = ""
    # Incident dispatcher: candidate alert emails are uploaded here as text files.
    ftp_host: str = ""
    ftp_port: int = 21
    ftp_user: str = ""
    ftp_password: str = ""
    ftp_dir: str = "/incidents"
    ftp_tls: bool = True
    dispatch_poll_seconds: int = 60
    # MR summarizer / MR reviewer: GitHub pull requests of this repository.
    github_token: str = ""
    github_repo: str = ""
    pr_poll_seconds: int = 120
    pr_max_diff_chars: int = 60000
    webhook_url: str = ""
    webhook_token: str = ""
    custom_agent: str = "agent.examples.my_agent:MyAgent"

    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    history_limit: int = 20

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
