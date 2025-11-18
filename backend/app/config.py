from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "coinatlas"
    db_password: str = "coinatlas_password"
    db_name: str = "coinatlas"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    class Config:
        env_file = ".env"


settings = Settings()
