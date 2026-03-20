"""
Grove — Sovereign Workspace Messaging
======================================
Slack-style channels, threads, and DMs — but every conversation
feeds Willow's knowledge graph and stays entirely local.

Port: 3000 (default)
Willow: http://localhost:8420
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Grove", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "app": "grove", "version": "0.1.0"}


# ΔΣ=42
# Seed planted by Shiva, 2026-03-03
# Next: channels, messages, threads, persona bridge, knowledge feed
