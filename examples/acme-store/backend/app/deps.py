"""Shared FastAPI dependencies."""

import sqlite3
from typing import Annotated

from fastapi import Depends

from app.db import get_db
from app.session import session_id

Db = Annotated[sqlite3.Connection, Depends(get_db)]
SessionId = Annotated[str, Depends(session_id)]
