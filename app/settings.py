"""
Тонкая обёртка над app.config для обратной совместимости.
Весь код, использующий settings.JWT_SECRET / settings.DATABASE_URL и т.д., продолжает работать.
"""
from app.config import app_config, db_config


class _Settings:
    @property
    def APP_NAME(self) -> str:
        return app_config.name

    @property
    def JWT_SECRET(self) -> str:
        return app_config.secret_key

    @property
    def JWT_ALG(self) -> str:
        return app_config.jwt_algorithm

    @property
    def ACCESS_TOKEN_EXPIRES_MIN(self) -> int:
        return app_config.token_expires_minutes

    @property
    def DATABASE_URL(self) -> str:
        return db_config.url


settings = _Settings()
