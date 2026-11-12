"""Vercel serverless entrypoint for the Sendwise FastAPI backend.

Mangum adapts our ASGI app. (MCP /mcp endpoint is mounted inside app.main.)
"""
from mangum import Mangum

from app.main import app

handler = Mangum(app, lifespan="off")
