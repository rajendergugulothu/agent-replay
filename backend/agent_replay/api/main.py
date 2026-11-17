"""Entry point for uvicorn: uvicorn agent_replay.api.main:app"""

import logging

from agent_replay.api.app import create_app
from agent_replay.config import Settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
app = create_app(Settings.from_env())
