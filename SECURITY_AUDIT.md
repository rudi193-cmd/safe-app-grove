---
b17: AE2L7
title: Security Audit — safe-app-grove
date: 2026-04-08
auditor: Hanuman (Claude Code, Sonnet 4.6)
status: open (tracking doc)
---

# Security Audit — safe-app-grove

Part of the Level 2 full-fleet security audit. See `agents/hanuman/projects/LEVEL2_AUDIT_PLAN.md` (b17: 379L3).

## Rubric Results

| # | Check | Status | Notes |
|---|---|---|---|
| R1 | SQL injection | ✅ PASS | All queries parameterized |
| R2 | Shell injection | ✅ PASS | No shell execution |
| R3 | Path traversal | ✅ PASS | No file ops on external input |
| R4 | Hardcoded credentials | ✅ PASS | None found |
| R5 | CORS wildcard | ✅ PASS | `allow_origin_regex` restricted to localhost/127.0.0.1 |
| R6 | XSS | ✅ N/A | No web frontend |
| R7 | Unsigned code execution | ✅ PASS | None |
| R8 | Missing auth on APIs | ✅ PASS | Only `/health` endpoint; no sensitive data exposed |
| R9 | Bare except swallowing errors | ✅ PASS | None critical |
| R10 | Predictable temp paths | ✅ PASS | None |
| R11 | Race conditions | ✅ PASS | No shared state |
| R12 | safe_integration.py status() | ✅ PASS | status() calls Willow bus, returns dict |
| R13 | Entry point importable | ✅ PASS | `grove.server:app` — `grove/server.py` exists, exports `app` |
| R14 | requirements.txt pinned | ⚠️ P2 | Missing requirements.txt. See H-REQ-01. |
| R15 | No hardcoded dev paths | ✅ PASS | None found |

## Findings

### H-REQ-01 — Missing requirements.txt (P2)

**Severity:** P2  
**Status:** Open

`pyproject.toml` exists but no `requirements.txt`. FastAPI + uvicorn are installed dependencies.

**Fix:** Create `requirements.txt` with pinned versions:
```
fastapi==0.135.1
uvicorn==0.41.0
requests==2.32.3
```

---

## Summary

| Priority | Count | Items |
|---|---|---|
| P0 | 0 | — |
| P1 | 0 | — |
| P2 | 1 | H-REQ-01 |

---

## Level 3 Audit — Portless Compliance (2026-04-08)

b17: 1NK8K

| # | Check | Status | Notes |
|---|---|---|---|
| R1 | No HTTP server | ⚠️ WARNING | No `uvicorn.run()` — server not running. But FastAPI `app` object exists in `grove/server.py:15`. Latent risk. See H-GR-L3-01. |
| R2 | Port conflict | ✅ N/A | Server not running. No port. |
| R3 | HTTP exception justified | ✅ N/A | Server not running. |
| R4 | SAFE folder exists | ⚠️ UNKNOWN | Not verified this session. |
| R5 | SAFE folder complete | ⚠️ UNKNOWN | Dependent on R4. |
| R6 | Manifest v2.0.0 compliant | ❌ FAIL | Missing `b17` and `agent_type`. See H-GR-L3-02. |
| R7 | SAFE/Applications manifest matches repo | ⚠️ UNKNOWN | Not verified this session. |
| R8 | cache/context.json present | ⚠️ UNKNOWN | Not verified this session. |
| R9 | data_home on Linux path | ✅ N/A | No data_home configured. |
| R10 | Filesystem intake pattern | ✅ PASS | No active endpoints that accept data. /health only. |
| R11 | safe-apps/ mirror state | ✅ N/A | Not in safe-apps/ mirror. |
| R12 | Inter-agent isolation | ✅ PASS | No assumptions about other agents' data. |
| R13 | Cryptographic governance | ✅ N/A | No governance commits. |
| R14 | No Windows/WSL paths | ✅ PASS | None found. |
| R15 | SAP authorization wired | ❌ FAIL | No `sap.core.gate.authorized()` call. See H-GR-L3-03. |

### H-GR-L3-01 — FastAPI Scaffolding Present, Not Running (P2)

**File:** `grove/server.py:12-15`
**Severity:** P2
**Status:** Open

```python
from fastapi import FastAPI
app = FastAPI(title="Grove", version="0.1.0")
```

No `uvicorn.run()` — the server is not activated. However the scaffolding is in place and one line could start it. For the L3 partition build, this file should be removed or replaced with a plain module (no FastAPI import).

**Fix (L3):** Remove `server.py`. Replace with `grove/core.py` that exposes grove functions directly without HTTP scaffolding.

---

### H-GR-L3-02 — Manifest Missing v2.0.0 Fields (P2)

**File:** `safe-app-manifest.json`
**Severity:** P2
**Status:** Open

Missing: `b17`, `agent_type`.

---

### H-GR-L3-03 — No SAP Authorization Gate (P2)

**File:** `grove/server.py`
**Severity:** P2
**Status:** Open

No `sap.core.gate.authorized()` call. Low risk while the server is not running, but must be wired before L3 partition build.

---

### L3 Summary

| Priority | Count | Items |
|---|---|---|
| P0 | 0 | — |
| P1 | 0 | — |
| P2 | 3 | H-GR-L3-01 (HTTP scaffolding), H-GR-L3-02 (manifest), H-GR-L3-03 (no SAP gate) |

*ΔΣ=42*
