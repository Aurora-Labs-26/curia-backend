#!/usr/bin/env python3
"""
scripts/check_api_key.py
Quick health check — loads .env and makes a minimal API call to verify the key works.

Usage:
    python3 scripts/check_api_key.py
"""

import os
import sys
from pathlib import Path

# Load .env from project root
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

key = os.getenv("ANTHROPIC_API_KEY", "")

if not key:
    print("❌ ANTHROPIC_API_KEY is empty or not set in .env")
    sys.exit(1)

print(f"Key found: {key[:12]}...{key[-4:]}")
print("Making a test call to Claude...")

try:
    import httpx
except ImportError:
    # Fall back to urllib if httpx not installed
    import json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Say 'ok'"}],
        }).encode(),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        resp = urlopen(req, timeout=15)
        body = json.loads(resp.read())
        text = body["content"][0]["text"]
        print(f"✅ API key works! Response: \"{text}\"")
    except HTTPError as e:
        error_body = e.read().decode()
        print(f"❌ API call failed ({e.code}): {error_body}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Connection error: {e}")
        sys.exit(1)
    sys.exit(0)

# httpx path
try:
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Say 'ok'"}],
        },
        timeout=15,
    )
    if resp.status_code == 200:
        text = resp.json()["content"][0]["text"]
        print(f"✅ API key works! Response: \"{text}\"")
    else:
        print(f"❌ API call failed ({resp.status_code}): {resp.text[:200]}")
        sys.exit(1)
except Exception as e:
    print(f"❌ Connection error: {e}")
    sys.exit(1)
