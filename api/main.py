from __future__ import annotations

import logging

from fastapi import FastAPI

from shared.settings import get_settings

from .routes import router

settings = get_settings()

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

app = FastAPI(title="yourbot-api", version="0.1.0")
app.include_router(router)
