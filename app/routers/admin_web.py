# Маршруты перемещены:
#   аутентификация  → app/routers/auth.py
#   владелец/admin  → app/routers/owner_web.py (с префиксом /owner)
from fastapi import APIRouter
router = APIRouter()
