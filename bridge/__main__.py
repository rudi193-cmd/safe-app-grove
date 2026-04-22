"""
Grove ↔ Matrix bridge.

Tokens are never passed as CLI args — they live in an env file or environment
variables so they stay out of shell history and process listings.

Usage:
  # Generate tokens once:
  python3 -c "import secrets; print(secrets.token_hex(32))"   # as_token
  python3 -c "import secrets; print(secrets.token_hex(32))"   # hs_token

  # Write to ~/.willow/bridge/tokens.env (chmod 600):
  GROVE_AS_TOKEN=<as_token>
  GROVE_HS_TOKEN=<hs_token>

  # Run:
  python3 -m bridge \\
    --homeserver https://matrix.example.com \\
    --hs-name    example.com \\
    --env-file   ~/.willow/bridge/tokens.env

  # Or via environment directly (e.g. systemd EnvironmentFile):
  GROVE_AS_TOKEN=... GROVE_HS_TOKEN=... python3 -m bridge ...

Then copy bridge/registration.yaml to your Synapse config dir and add it to
homeserver.yaml under `app_service_config_files`.
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path

from .app import GroveMatrixBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-22s  %(levelname)s  %(message)s",
)


def _load_env_file(path: Path) -> None:
    """Parse a simple KEY=VALUE env file into os.environ. Ignores comments and blanks."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"Error: {name} is not set.\n"
            f"Add it to your --env-file or set it as an environment variable."
        )
    return value


def main() -> None:
    p = argparse.ArgumentParser(
        description="Grove ↔ Matrix bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--homeserver", required=True, help="Matrix homeserver URL")
    p.add_argument("--hs-name",    required=True, help="Homeserver name (e.g. example.com)")
    p.add_argument("--env-file",   default=None,  help="Path to KEY=VALUE env file for tokens")
    p.add_argument("--grove-port", type=int, default=8551, help="u2u listen port (default 8551)")
    p.add_argument("--as-port",    type=int, default=8560, help="AS HTTP server port (default 8560)")
    p.add_argument("--data-dir",   default="~/.willow/bridge", help="State directory")
    args = p.parse_args()

    if args.env_file:
        env_path = Path(args.env_file).expanduser()
        if not env_path.exists():
            raise SystemExit(f"Error: env file not found: {env_path}")
        _load_env_file(env_path)

    as_token = _require_env("GROVE_AS_TOKEN")
    hs_token = _require_env("GROVE_HS_TOKEN")

    data = Path(args.data_dir).expanduser()
    data.mkdir(parents=True, exist_ok=True)

    bridge = GroveMatrixBridge(
        homeserver    = args.homeserver,
        hs_name       = args.hs_name,
        as_token      = as_token,
        hs_token      = hs_token,
        grove_port    = args.grove_port,
        as_port       = args.as_port,
        identity_path = data / "identity.json",
        store_path    = data / "bridge.db",
    )

    print("Grove ↔ Matrix bridge starting")
    print(f"  homeserver  → {args.homeserver}")
    print(f"  AS server   → http://0.0.0.0:{args.as_port}")
    print(f"  u2u port    → {args.grove_port}")
    print(f"  data dir    → {data}")
    print()

    asyncio.run(bridge.run())


if __name__ == "__main__":
    main()
