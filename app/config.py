from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CERTGEN_")

    database_url: str = "sqlite:///./certificates.db"
    storage_dir: str = "./storage"
    max_recipients_per_job: int = 5000


settings = Settings()
