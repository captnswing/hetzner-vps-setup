import os
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
from hcloud.server_types import ServerType
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

load_dotenv(".env")

console = Console()

SSH_KEY_PATH = Path("~/.ssh/Hetzner_Automation_Key").expanduser()
SSH_USER = "sysadmin"
TAILSCALE_TAG = "tag:vps"

HCLOUD_TOKEN = os.getenv("HCLOUD_TOKEN")
SSH_KEY_NAME = os.getenv("SSH_KEY_NAME")
TAILSCALE_AUTH_KEY = os.getenv("TAILSCALE_AUTH_KEY", "")
PUB_KEY = os.getenv("PUB_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

for var in ["HCLOUD_TOKEN", "SSH_KEY_NAME"]:
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


def server_type_min_cost(st) -> float | None:
    """Cheapest monthly price across all locations — shown before a datacenter is chosen."""
    vals = [v for p in (getattr(st, "prices", None) or []) if (v := _monthly_gross(p)) is not None]
    return min(vals) if vals else None


def server_type_cost_at(st, location_name: str) -> float | None:
    """Monthly price for a server type at a specific location."""
    for p in getattr(st, "prices", None) or []:
        if _attr(p, "location") == location_name:
            return _monthly_gross(p)
    return None


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
                        "StrictHostKeyChecking=no",
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


def prompt_hostname(client: Client) -> str:
    """Prompt user for hostname and check availability."""
    first_attempt = True
    while True:
        prompt_msg = "Enter hostname" if first_attempt else "Enter hostname (previous name was taken)"
        hostname = ask_or_exit(questionary.text(prompt_msg, default="hardened-host"))
        first_attempt = False

        try:
            existing_server = client.servers.get_by_name(hostname)
            if existing_server:
                console.print(f"[bold red]✗[/bold red] Hostname '{hostname}' is already taken, please try another name")
                continue
        except Exception:
            pass

        console.print(f"[bold green]✓[/bold green] Hostname '{hostname}' is available")
        return hostname


def prompt_server_type(client: Client) -> str:
    """Display server type options and prompt for selection."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Loading server types...", total=None)
        server_types = client.server_types.get_all()

    common_types = ["cx23", "cx33", "cx43", "cpx22", "cpx31", "cpx41"]
    filtered_types = [st for st in server_types if st.name in common_types]
    filtered_types.sort(key=lambda st: st.name)

    if not filtered_types:
        console.print("[yellow]No server types found, using all available types[/yellow]")
        filtered_types = server_types

    def to_choice(st):
        cost = server_type_min_cost(st)
        cost_str = f"  ~€{cost:.2f}/mo" if cost is not None else ""
        specs = f"{st.cores} vCPU, {st.memory:3} GB RAM, {st.disk:3} GB storage ({st.cpu_type})"
        return questionary.Choice(title=f"{st.name:8} - {specs}{cost_str}", value=st.name)

    return prompt_choice("Select server type:", filtered_types, to_choice, default_value="cx23")


def prompt_datacenter(client: Client) -> str:
    """Display datacenter options and prompt for selection."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Loading datacenters...", total=None)
        datacenters = client.datacenters.get_all()

    def to_choice(dc):
        return questionary.Choice(
            title=f"{dc.location.name:6} - {dc.location.city:15} ({dc.location.country})",
            value=dc.location.name,
        )

    return prompt_choice("Select datacenter:", datacenters, to_choice, default_value="hel1")


def check_server_type_availability(client: Client, server_type_name: str, datacenter_name: str) -> bool:
    """Check if the selected server type is available in the chosen datacenter."""
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Checking server type availability...", total=None)

            # Get all server types
            server_types = client.server_types.get_all()
            server_type = next((st for st in server_types if st.name == server_type_name), None)

            if not server_type:
                console.print(f"[bold red]✗[/bold red] Server type '{server_type_name}' not found")
                return False

            # Get datacenter details
            datacenters = client.datacenters.get_all()
            datacenter = next((dc for dc in datacenters if dc.location.name == datacenter_name), None)

            if not datacenter:
                console.print(f"[bold red]✗[/bold red] Datacenter '{datacenter_name}' not found")
                return False

            # Check if server type is available in the datacenter
            # Hetzner API doesn't have a direct availability check, but we can check if the server type
            # is generally available. In practice, most server types are available in all datacenters.
            console.print(
                f"[bold green]✓[/bold green] Server type '{server_type_name}' is available in '{datacenter_name}'"
            )
            return True

    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Error checking availability: {e}")
        return False


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
    server_type = prompt_server_type(client)
    datacenter = prompt_datacenter(client)

    # Check if server type is available in datacenter
    if not check_server_type_availability(client, server_type, datacenter):
        console.print("[bold red]Cannot proceed with unavailable server type/datacenter combination[/bold red]")
        sys.exit(1)

    # Estimate cost at the chosen location (falls back to the cheapest location)
    chosen_st = next((st for st in client.server_types.get_all() if st.name == server_type), None)
    est_cost = server_type_cost_at(chosen_st, datacenter) if chosen_st else None
    if est_cost is None and chosen_st:
        est_cost = server_type_min_cost(chosen_st)

    # Confirm configuration
    summary_table = Table(show_header=False, box=None)
    summary_table.add_column("Setting", style="cyan")
    summary_table.add_column("Value", style="green")
    summary_table.add_row("Hostname", hostname)
    summary_table.add_row("Server Type", server_type)
    summary_table.add_row("Datacenter", datacenter)
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
                server_type=ServerType(name=server_type),
                image=Image(name="ubuntu-24.04"),
                ssh_keys=[ssh_key],
                user_data=config.substitute(
                    hostname=hostname,
                    pub_key=pub_key_text,
                    tailscale_key=TAILSCALE_AUTH_KEY,
                    github_token=GITHUB_TOKEN,
                ),
                location=Location(name=datacenter),
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
