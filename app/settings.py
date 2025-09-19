from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "License Server"
    JWT_SECRET: str = "CHANGE_ME"   # замени
    JWT_ALG: str = "HS256"
    ACCESS_TOKEN_EXPIRES_MIN: int = 60*8
    DATABASE_URL: str = "sqlite+aiosqlite:///./licserver.db"

    class Config:
        env_file = ".env"

settings = Settings()
