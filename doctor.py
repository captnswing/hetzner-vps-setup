#!/usr/bin/env python
"""Preflight check — verify every prerequisite before provisioning.

Reads credentials from the environment. It does not care *how* they got there:
populate a plain `.env` (see `.env.example`) and run `make doctor`, or inject them
with your own secrets manager (the maintainer uses `op-run -- make doctor`). Either
way doctor only ever inspects `os.environ`, so neither path is imposed on the other.

Exit status is 0 when every required check passes, 1 otherwise.
"""

import os
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv(".env")

console = Console()

# PUB_KEY is intentionally not required: setup-vps.py can source the public key from
# the existing Hetzner key, a local .pub, or by generating a fresh keypair.
REQUIRED_VARS = ["HCLOUD_TOKEN", "SSH_KEY_NAME", "TAILSCALE_AUTH_KEY"]

# Each check appends (name, status, note) where status is "ok" | "fail" | "warn".
Result = tuple[str, str, str]


def _tailscale_logged_in() -> bool:
    try:
        return subprocess.run(["tailscale", "status"], capture_output=True, timeout=10).returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def check_local_tools() -> list[Result]:
    results: list[Result] = []
    results.append(
        ("uv installed", "ok", "")
        if shutil.which("uv")
        else ("uv installed", "fail", "Install: https://docs.astral.sh/uv/getting-started/installation/")
    )
    if not shutil.which("tailscale"):
        results.append(("Tailscale CLI installed", "fail", "Install: brew install tailscale"))
    elif not _tailscale_logged_in():
        results.append(
            ("Tailscale logged in", "fail", "Open the Tailscale app and sign in (`tailscale status` to verify)")
        )
    else:
        results.append(("Tailscale CLI + login", "ok", ""))
    return results


def check_env_vars() -> list[Result]:
    results: list[Result] = []
    for var in REQUIRED_VARS:
        if os.getenv(var):
            results.append((f"${var} set", "ok", ""))
        else:
            results.append((f"${var} set", "fail", "Set it in .env (cp .env.example .env) or inject via op-run"))
    if not os.getenv("GITHUB_TOKEN"):
        results.append(("$GITHUB_TOKEN set", "warn", "Optional — only needed for gh CLI / GHCR auto-login on the box"))
    return results


def check_pub_key() -> list[Result]:
    pub = os.getenv("PUB_KEY", "")
    if not pub:
        return []  # already reported as a missing env var
    if pub.startswith(("ssh-", "ecdsa-", "sk-")):
        return [("$PUB_KEY looks like a public key", "ok", "")]
    return [
        (
            "$PUB_KEY looks like a public key",
            "warn",
            "Expected the PUBLIC key text (ssh-ed25519 AAAA...), not a path or private key",
        )
    ]


def check_local_private_key() -> list[Result]:
    key = Path("~/.ssh/Hetzner_Automation_Key").expanduser()
    if key.exists():
        return [("Local private key present", "ok", str(key))]
    return [("Local private key present", "warn", f"{key} not found — needed to SSH in after provisioning")]


def check_hetzner() -> list[Result]:
    """Validate the Hetzner token and that the named SSH key exists in the account."""
    token = os.getenv("HCLOUD_TOKEN")
    if not token:
        return []  # missing token already reported

    from hcloud import Client
    from hcloud._exceptions import APIException

    client = Client(token=token)
    try:
        client.locations.get_all()
    except APIException as e:
        return [
            (
                "Hetzner token valid",
                "fail",
                f"{e.code}: {e.message} — regenerate at console.hetzner.cloud → Security → API Tokens",
            )
        ]
    except Exception as e:
        return [("Hetzner token valid", "fail", f"Could not reach Hetzner API: {e}")]

    results: list[Result] = [("Hetzner token valid", "ok", "")]

    key_name = os.getenv("SSH_KEY_NAME")
    if key_name:
        try:
            found = client.ssh_keys.get_by_name(key_name)
        except Exception as e:
            found = None
            results.append((f"SSH key '{key_name}' in Hetzner", "fail", f"Lookup failed: {e}"))
        else:
            if found:
                results.append((f"SSH key '{key_name}' in Hetzner", "ok", ""))
            else:
                results.append(
                    (
                        f"SSH key '{key_name}' in Hetzner",
                        "warn",
                        "Not uploaded yet — setup-vps.py will offer to create & upload it",
                    )
                )
    return results


def main() -> None:
    console.print("[bold cyan]Hetzner VPS Setup — preflight[/bold cyan]\n")

    results: list[Result] = []
    results += check_local_tools()
    results += check_env_vars()
    results += check_pub_key()
    results += check_local_private_key()
    results += check_hetzner()

    symbols = {"ok": "[green]✓[/green]", "fail": "[red]✗[/red]", "warn": "[yellow]•[/yellow]"}
    table = Table(show_header=True, header_style="bold")
    table.add_column("", width=2)
    table.add_column("Check")
    table.add_column("Fix / note", style="dim")
    for name, status, note in results:
        table.add_row(symbols[status], name, note)
    console.print(table)

    failures = sum(1 for _, status, _ in results if status == "fail")
    warnings = sum(1 for _, status, _ in results if status == "warn")
    console.print()
    if failures:
        console.print(f"[bold red]✗ {failures} check(s) failed[/bold red] — fix the above, then re-run `make doctor`.")
        raise SystemExit(1)
    msg = "[bold green]✓ All required checks passed[/bold green] — ready to provision: [bold]uv run setup-vps.py[/bold]"
    if warnings:
        msg += f" [dim]({warnings} optional warning(s))[/dim]"
    console.print(msg)


if __name__ == "__main__":
    main()
