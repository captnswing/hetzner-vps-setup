import os
import re
import subprocess
import sys
import time
from pathlib import Path
from string import Template

import qrcode
import questionary
from dotenv import load_dotenv
from hcloud import Client
from hcloud._exceptions import APIException
from hcloud.images import Image
from hcloud.locations import Location
from hcloud.server_types import BoundServerType, ServerType
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

load_dotenv(".env")

# `op run` pipes stdout (to mask secrets), so Rich sees a non-tty and drops color.
# When a real terminal is still attached via stdin, force color on; leave it to Rich's
# auto-detection otherwise (so genuine redirection / CI stays plain).
_force_color = True if (not sys.stdout.isatty() and sys.stdin.isatty()) else None
console = Console(force_terminal=_force_color)

SSH_KEY_PATH = Path("~/.ssh/Hetzner_Automation_Key").expanduser()
SSH_USER = "sysadmin"
TAILSCALE_TAG = "tag:vps"
# One DNS label (RFC 1123): Tailscale and MagicDNS use it as the node name.
HOSTNAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")

HCLOUD_TOKEN = os.getenv("HCLOUD_TOKEN")
SSH_KEY_NAME = os.getenv("SSH_KEY_NAME")
TAILSCALE_AUTH_KEY = os.getenv("TAILSCALE_AUTH_KEY", "")
PUB_KEY = os.getenv("PUB_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

for var in ["HCLOUD_TOKEN", "SSH_KEY_NAME", "TAILSCALE_AUTH_KEY"]:
    if not os.getenv(var):
        console.print(f"[bold red]Error:[/bold red] {var} not found in environment")
        sys.exit(1)


def ask_or_exit(question):
    """Execute questionary prompt and exit gracefully on Ctrl+C."""
    result = question.ask()
    if result is None:
        sys.exit(0)
    return result


def prompt_choice(label, items, to_choice_fn, default_value=None):
    """Generic helper for selecting from a list with spinner loading."""
    choices = [to_choice_fn(item) for item in items]

    if default_value:
        default = default_value if any(c.value == default_value for c in choices) else choices[0].value
    else:
        default = choices[0].value if choices else None

    return ask_or_exit(questionary.select(label, choices=choices, default=default, use_arrow_keys=True))


def _attr(obj, name):
    """Read an attribute whether obj is a plain object or a dict (hcloud is inconsistent)."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _monthly_gross(st_price) -> float | None:
    """Gross monthly price (incl. VAT) from one ServerType price entry, or None."""
    price_monthly = _attr(st_price, "price_monthly")
    gross = _attr(price_monthly, "gross") if price_monthly is not None else None
    try:
        return float(gross) if gross is not None else None
    except (TypeError, ValueError):
        return None


def server_type_cost_at(st, location_name: str) -> float | None:
    """Monthly price for a server type at a specific location."""
    for p in getattr(st, "prices", None) or []:
        if _attr(p, "location") == location_name:
            return _monthly_gross(p)
    return None


def available_at(st: BoundServerType, location_name: str) -> bool:
    """Whether a server type can be ordered at a location right now (not sold out or deprecated there)."""
    return any(
        sl.location.name == location_name and sl.available and sl.deprecation is None for sl in st.locations or []
    )


def print_ssh_qr(user: str, ip: str) -> None:
    """Print a scannable QR encoding an ssh:// URI — any SSH client app can ingest it."""
    qr = qrcode.QRCode(border=1)
    qr.add_data(f"ssh://{user}@{ip}")
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def maybe_write_ssh_config(hostname: str, ip: str) -> None:
    """Offer to append a Host block to ~/.ssh/config so `ssh <hostname>` just works."""
    config_path = Path("~/.ssh/config").expanduser()
    existing = config_path.read_text() if config_path.exists() else ""
    if f"Host {hostname}\n" in existing or f"Host {hostname} " in existing:
        console.print(f"[dim]~/.ssh/config already has a 'Host {hostname}' entry — leaving it untouched.[/dim]")
        return

    if not ask_or_exit(questionary.confirm(f"Add '{hostname}' to ~/.ssh/config?", default=True)):
        return

    block = f"\nHost {hostname}\n    HostName {ip}\n    User {SSH_USER}\n    IdentityFile {SSH_KEY_PATH}\n"
    config_path.parent.mkdir(mode=0o700, exist_ok=True)
    with config_path.open("a") as f:
        f.write(block)
    console.print(f"[bold green]✓[/bold green] Added — connect with: [bold]ssh {hostname}[/bold]")


def print_connection_info(hostname: str, ip: str) -> None:
    """Show ssh + mosh commands and a phone-scannable QR."""
    console.print("\n[bold cyan]Connect:[/bold cyan]")
    # print() (not console.print) to keep the commands copy-paste clean, no markup parsing.
    print(f"  ssh -i {SSH_KEY_PATH} {SSH_USER}@{ip}")
    print(f'  mosh --ssh="ssh -i {SSH_KEY_PATH}" {SSH_USER}@{ip}   # roaming-friendly, great from a phone')

    console.print("\n[bold cyan]Scan to connect from your phone[/bold cyan] (any SSH client):")
    print_ssh_qr(SSH_USER, ip)
    console.print(f"[dim]Encodes ssh://{SSH_USER}@{ip}[/dim]")


def generate_keypair() -> str | None:
    """ssh-keygen a new ed25519 keypair at SSH_KEY_PATH; return its public key text."""
    SSH_KEY_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(SSH_KEY_PATH), "-N", "", "-C", "hetzner-automation"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        console.print("[bold red]ssh-keygen not found.[/bold red]")
        return None
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]ssh-keygen failed:[/bold red] {e.stderr.strip()}")
        return None
    console.print(f"[bold green]✓[/bold green] Generated keypair at {SSH_KEY_PATH}")
    return Path(f"{SSH_KEY_PATH}.pub").read_text().strip()


def resolve_public_key_material() -> str | None:
    """Find public-key text to upload: $PUB_KEY, a local .pub, or a freshly generated keypair."""
    if PUB_KEY and PUB_KEY.startswith(("ssh-", "ecdsa-", "sk-")):
        console.print("[cyan]Using public key from $PUB_KEY.[/cyan]")
        return PUB_KEY.strip()

    pub_path = Path(f"{SSH_KEY_PATH}.pub")
    if pub_path.exists():
        console.print(f"[cyan]Using public key from {pub_path}.[/cyan]")
        return pub_path.read_text().strip()

    if ask_or_exit(
        questionary.confirm(f"No public key available. Generate a new ed25519 keypair at {SSH_KEY_PATH}?", default=True)
    ):
        return generate_keypair()
    return None


def ensure_ssh_key(client: Client):
    """Return the Hetzner SSH key named SSH_KEY_NAME, creating + uploading it if absent."""
    existing = client.ssh_keys.get_by_name(SSH_KEY_NAME)
    if existing:
        return existing

    console.print(f"[yellow]SSH key '{SSH_KEY_NAME}' is not in your Hetzner account yet.[/yellow]")
    pub_text = resolve_public_key_material()
    if not pub_text:
        console.print("[bold red]Cannot proceed without an SSH key.[/bold red]")
        sys.exit(1)

    if not ask_or_exit(questionary.confirm(f"Upload this public key to Hetzner as '{SSH_KEY_NAME}'?", default=True)):
        sys.exit(0)
    try:
        created = client.ssh_keys.create(name=SSH_KEY_NAME, public_key=pub_text)
    except APIException as e:
        console.print(f"[bold red]Failed to upload SSH key:[/bold red] {e.code} - {e.message}")
        sys.exit(1)
    console.print(f"[bold green]✓[/bold green] Uploaded SSH key '{SSH_KEY_NAME}' to Hetzner.")
    return created


def get_tailscale_ip(hostname: str, timeout: int = 300) -> str | None:
    """
    Polls the local Tailscale CLI to find the IP of the new node.
    Requires 'tailscale' to be in your local PATH.
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        progress.add_task(
            f"Step 2/3 · Waiting for Tailscale registration for '{hostname}' (timeout: {timeout}s)...",
            total=None,
        )
        start = time.time()
        while time.time() - start < timeout:
            try:
                # Ask local tailscale daemon for the IP of the hostname
                result = subprocess.run(["tailscale", "ip", "-4", hostname], capture_output=True, text=True)
                # If successful, we got an IP
                if result.returncode == 0:
                    progress.stop()
                    return result.stdout.strip()
            except FileNotFoundError:
                console.print("[yellow]Warning:[/yellow] 'tailscale' CLI not found locally. Cannot resolve VPN IP.")
                return None

            # Wait a bit before retrying
            time.sleep(5)
    return None


def wait_for_ssh(ip: str, timeout: int = 300) -> bool:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        progress.add_task(f"Step 3/3 · Waiting for SSH on {ip} (timeout: {timeout}s)...", total=None)
        start = time.time()
        while time.time() - start < timeout:
            try:
                result = subprocess.run(
                    [
                        "ssh",
                        "-i",
                        str(SSH_KEY_PATH),
                        "-o",
                        "ConnectTimeout=5",
                        "-o",
                        "StrictHostKeyChecking=accept-new",
                        "-o",
                        "BatchMode=yes",
                        f"sysadmin@{ip}",
                        "echo ready",
                    ],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return True
            except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                pass
            time.sleep(5)
    return False


def tailnet_has_node(hostname: str) -> bool:
    """True if the tailnet already has a node by this name, e.g. a deleted box never removed from Tailscale.

    A new node would then register as `<hostname>-1`, and `tailscale ip <hostname>` would return the old IP.
    """
    try:
        result = subprocess.run(["tailscale", "ip", "-4", hostname], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def hostname_problem(client: Client, hostname: str) -> str | None:
    """Return why a hostname can't be used, or None if it's fine."""
    if not HOSTNAME_RE.fullmatch(hostname):
        return (
            "is not a valid hostname (lowercase letters, digits and hyphens; max 63 chars; no leading/trailing hyphen)"
        )
    try:
        if client.servers.get_by_name(hostname):
            return "is already taken by a server in your Hetzner project"
    except Exception:
        pass
    if tailnet_has_node(hostname):
        return "is already a node in your tailnet — remove it at https://login.tailscale.com/admin/machines first"
    return None


def prompt_hostname(client: Client) -> str:
    """Prompt user for hostname and check it is valid and unused in Hetzner and the tailnet."""
    while True:
        hostname = ask_or_exit(questionary.text("Enter hostname", default="hardened-host")).strip()
        if problem := hostname_problem(client, hostname):
            console.print(f"[bold red]✗[/bold red] '{hostname}' {problem}")
            continue
        console.print(f"[bold green]✓[/bold green] Hostname '{hostname}' is available")
        return hostname


def prompt_location(client: Client, server_types: list[BoundServerType]) -> str:
    """Prompt for a location, offering only those where at least one server type can be ordered."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Loading locations...", total=None)
        locations = client.locations.get_all()

    locations = [loc for loc in locations if any(available_at(st, loc.name) for st in server_types)]
    if not locations:
        console.print("[bold red]Error:[/bold red] No server types are available in any location right now")
        sys.exit(1)

    def to_choice(loc):
        return questionary.Choice(title=f"{loc.name:6} - {loc.city:15} ({loc.country})", value=loc.name)

    return prompt_choice("Select location:", locations, to_choice, default_value="hel1")


def prompt_server_type(server_types: list[BoundServerType], location_name: str) -> BoundServerType:
    """Prompt for a server type among those available at the location, cheapest first."""
    available = [st for st in server_types if available_at(st, location_name)]
    available.sort(key=lambda st: (server_type_cost_at(st, location_name) or float("inf"), st.name))

    def to_choice(st):
        cost = server_type_cost_at(st, location_name)
        cost_str = f"  ~€{cost:.2f}/mo" if cost is not None else ""
        specs = f"{st.cores:2} vCPU, {st.memory:3g} GB RAM, {st.disk:3} GB storage ({st.architecture}, {st.cpu_type})"
        return questionary.Choice(title=f"{st.name:8} - {specs}{cost_str}", value=st.name)

    name = prompt_choice("Select server type:", available, to_choice, default_value="cx23")
    return next(st for st in available if st.name == name)


def main() -> None:
    console.print(Panel.fit("[bold cyan]🚀 Hetzner VPS Setup 🚀[/bold cyan]", border_style="cyan"))

    client = Client(token=HCLOUD_TOKEN)

    # Ensure the SSH key exists in Hetzner (create + upload it if missing)
    ssh_key = ensure_ssh_key(client)
    # Source the public key from Hetzner so the key installed on the box always
    # matches the one Hetzner has on file (whether it pre-existed or we just uploaded it).
    pub_key_text = ssh_key.public_key

    # Interactive prompts
    hostname = prompt_hostname(client)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Loading server types...", total=None)
        server_types = client.server_types.get_all()

    location = prompt_location(client, server_types)
    server_type = prompt_server_type(server_types, location)
    est_cost = server_type_cost_at(server_type, location)

    # Confirm configuration
    summary_table = Table(show_header=False, box=None)
    summary_table.add_column("Setting", style="cyan")
    summary_table.add_column("Value", style="green")
    summary_table.add_row("Hostname", hostname)
    summary_table.add_row("Server Type", server_type.name)
    summary_table.add_row("Location", location)
    summary_table.add_row("Est. cost", f"~€{est_cost:.2f}/mo" if est_cost is not None else "n/a")
    summary_table.add_row("Tailscale tag", TAILSCALE_TAG)
    console.print(Panel(summary_table, title="[bold cyan]Configuration Summary[/bold cyan]", border_style="cyan"))
    console.print()

    proceed = ask_or_exit(questionary.confirm("Proceed with server creation?", default=True))

    if not proceed:
        console.print("[yellow]Cancelled by user[/yellow]")
        sys.exit(0)

    # Load cloud-init configuration
    config = Template((Path(__file__).parent / "cloud-config.yaml.tmpl").read_text())

    # Create server
    console.print(f"\n[bold cyan]Creating server: {hostname}[/bold cyan]")

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            progress.add_task("Step 1/3 · Provisioning server...", total=None)

            response = client.servers.create(
                name=hostname,
                server_type=ServerType(name=server_type.name),
                image=Image(name="ubuntu-24.04"),
                ssh_keys=[ssh_key],
                user_data=config.substitute(
                    hostname=hostname,
                    pub_key=pub_key_text,
                    tailscale_key=TAILSCALE_AUTH_KEY,
                    github_token=GITHUB_TOKEN,
                ),
                location=Location(name=location),
            )
    except APIException as e:
        console.print(f"[bold red]Failed to create server:[/bold red] {e.code} - {e.message}")
        sys.exit(1)

    if not response.server.public_net or not response.server.public_net.ipv4:
        console.print("[bold red]Error:[/bold red] Server created but no IP address assigned")
        sys.exit(1)

    public_ip = response.server.public_net.ipv4.ip
    console.print(f"[bold green]✓[/bold green] Server {hostname} created with public IP: {public_ip}")

    # Try to get Tailscale IP
    ts_ip = get_tailscale_ip(hostname)
    if ts_ip:
        console.print(f"[bold green]✓[/bold green] Tailscale node found: {ts_ip}")
        console.print("[cyan]Waiting for server to complete reboot and allow VPN SSH...[/cyan]")

        # Wait for SSH on that IP
        ssh_ready = wait_for_ssh(ts_ip)

        if ssh_ready:
            console.print(f"\n[bold green]✓ SSH ready on host {hostname} / {ts_ip}![/bold green]")
            print_connection_info(hostname, ts_ip)
            console.print()
            maybe_write_ssh_config(hostname, ts_ip)
        else:
            console.print("[yellow]SSH not ready yet (timeout)[/yellow]")
            console.print("[cyan]Try connecting manually:[/cyan]")
            print(f"  ssh -i {SSH_KEY_PATH} {SSH_USER}@{ts_ip}")
    else:
        console.print("[yellow]Could not resolve Tailscale IP[/yellow]")
        console.print("[cyan]Is Tailscale started on your computer?[/cyan]")


if __name__ == "__main__":
    main()
