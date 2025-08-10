#!/usr/bin/env python3
"""Interactive generator for .env tailored to this project.

Includes:
- Admin login (for /auth + /admin)
- Netdata proxy base URL
- Rate limiting knobs
- App port and token DB path

Re-runnable: existing .env values are used as defaults.
"""
from pathlib import Path
import typer
from typing import Dict

try:
    from dotenv import dotenv_values
except Exception:  # keep working even if python-dotenv isn't installed yet
    def dotenv_values(path):  # type: ignore
        values = {}
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        values[k.strip()] = v.strip()
        except FileNotFoundError:
            pass
        return values

app = typer.Typer()

DEFAULTS: Dict[str, str] = {
    # --- Admin auth (used by /auth + guarding /admin & /netdata) ---
    "ADMIN_USER": "admin",
    "ADMIN_PASS": "changeme",
    "AUTH_SECRET": "",  # optional; random fallback will be used if empty

    # --- App server ---
    "PORT": "6969",

    # --- API tokens storage (admin UI) ---
    "TOKENS_DB_URL": "sqlite:///./api_tokens.db",

    # --- Netdata reverse proxy base (local Netdata default) ---
    "NETDATA_BASE": "http://127.0.0.1:19999",

    # --- Pushcut integration ---
    "PUSHCUT_URL": "",
    "NETDATA_MONITOR": "0",
    "NETDATA_POLL_SEC": "5",
    # --- System metrics thresholds (used by Netdata monitoring) ---
    "STRESS_CPU_PCT": "85",
    "STRESS_MEM_PCT": "90",
    "STRESS_LOAD_MULT": "1.5",
    "STRESS_SUSTAIN_SECS": "120",

    # --- Rate limiting knobs ---
    "RATE_LIMIT_IP_PER_MIN": "30",     # low/anonymous
    "RATE_LIMIT_TOKEN_PER_MIN": "300", # higher/known token
    "RATE_LIMIT_WINDOW_SEC": "60",
}


def _load_existing(path: Path) -> Dict[str, str]:
    return dict(dotenv_values(str(path))) if path.exists() else {}


@app.command()
def run():
    """Interactive configuration to create/update a .env file for this project."""
    env_path = Path(".env")
    existing = _load_existing(env_path)

    typer.echo(f"Writing {env_path.resolve()} …")

    config: Dict[str, str] = {}

    # Admin auth
    admin_user = typer.prompt("ADMIN_USER", default=existing.get("ADMIN_USER", DEFAULTS["ADMIN_USER"]))
    admin_pass = typer.prompt("ADMIN_PASS", default=existing.get("ADMIN_PASS", DEFAULTS["ADMIN_PASS"]), hide_input=True)
    auth_secret = typer.prompt(
        "AUTH_SECRET (leave blank to auto-generate at runtime)",
        default=existing.get("AUTH_SECRET", DEFAULTS["AUTH_SECRET"]) or "",
        hide_input=True,
    )
    config["ADMIN_USER"], config["ADMIN_PASS"], config["AUTH_SECRET"] = admin_user, admin_pass, auth_secret

    # App server
    config["PORT"] = typer.prompt("PORT", default=existing.get("PORT", DEFAULTS["PORT"]))

    # Token DB
    config["TOKENS_DB_URL"] = typer.prompt(
        "TOKENS_DB_URL",
        default=existing.get("TOKENS_DB_URL", DEFAULTS["TOKENS_DB_URL"])
    )

    # Netdata
    config["NETDATA_BASE"] = typer.prompt(
        "NETDATA_BASE",
        default=existing.get("NETDATA_BASE", DEFAULTS["NETDATA_BASE"])
    )

    # Pushcut URL
    config["PUSHCUT_URL"] = typer.prompt(
        "PUSHCUT_URL (Pushcut notification URL, leave blank to disable)",
        default=existing.get("PUSHCUT_URL", DEFAULTS["PUSHCUT_URL"]),
        hide_input=False,
    )

    config["NETDATA_MONITOR"] = typer.prompt(
        "NETDATA_MONITOR (1 to enable background polling/alerts; 0 to disable)",
        default=existing.get("NETDATA_MONITOR", DEFAULTS["NETDATA_MONITOR"]) ,
    )

    config["NETDATA_POLL_SEC"] = typer.prompt(
        "NETDATA_POLL_SEC (polling interval in seconds)",
        default=existing.get("NETDATA_POLL_SEC", DEFAULTS["NETDATA_POLL_SEC"]) ,
    )

    # System metrics thresholds (used by Netdata monitoring)
    config["STRESS_CPU_PCT"] = typer.prompt(
        "STRESS_CPU_PCT (CPU percent threshold)",
        default=existing.get("STRESS_CPU_PCT", DEFAULTS["STRESS_CPU_PCT"]) ,
    )
    config["STRESS_MEM_PCT"] = typer.prompt(
        "STRESS_MEM_PCT (Memory percent threshold)",
        default=existing.get("STRESS_MEM_PCT", DEFAULTS["STRESS_MEM_PCT"]) ,
    )
    config["STRESS_LOAD_MULT"] = typer.prompt(
        "STRESS_LOAD_MULT (load1 >= cores * multiplier)",
        default=existing.get("STRESS_LOAD_MULT", DEFAULTS["STRESS_LOAD_MULT"]) ,
    )
    config["STRESS_SUSTAIN_SECS"] = typer.prompt(
        "STRESS_SUSTAIN_SECS (seconds to sustain before alert)",
        default=existing.get("STRESS_SUSTAIN_SECS", DEFAULTS["STRESS_SUSTAIN_SECS"]) ,
    )

    # Rate limits
    config["RATE_LIMIT_IP_PER_MIN"] = typer.prompt(
        "RATE_LIMIT_IP_PER_MIN (anonymous per minute)",
        default=existing.get("RATE_LIMIT_IP_PER_MIN", DEFAULTS["RATE_LIMIT_IP_PER_MIN"])
    )
    config["RATE_LIMIT_TOKEN_PER_MIN"] = typer.prompt(
        "RATE_LIMIT_TOKEN_PER_MIN (per token per minute)",
        default=existing.get("RATE_LIMIT_TOKEN_PER_MIN", DEFAULTS["RATE_LIMIT_TOKEN_PER_MIN"])
    )
    config["RATE_LIMIT_WINDOW_SEC"] = typer.prompt(
        "RATE_LIMIT_WINDOW_SEC (window size in seconds)",
        default=existing.get("RATE_LIMIT_WINDOW_SEC", DEFAULTS["RATE_LIMIT_WINDOW_SEC"])
    )

    # Write .env
    with env_path.open("w") as f:
        for k, v in config.items():
            f.write(f"{k}={v}\n")

    typer.echo(f"✅ Wrote {env_path} with {len(config)} keys.")


if __name__ == "__main__":
    app()