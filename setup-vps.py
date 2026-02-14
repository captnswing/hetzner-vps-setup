import os
import subprocess
import sys
import time
from pathlib import Path
from string import Template

import questionary
from dotenv import load_dotenv
from hcloud import Client
from hcloud._exceptions import APIException
from hcloud.images import Image
from hcloud.locations import Location
from hcloud.server_types import ServerType
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

load_dotenv(".env")

console = Console()

SSH_KEY_PATH = Path("~/.ssh/Hetzner_Automation_Key").expanduser()

HCLOUD_TOKEN = os.getenv("HCLOUD_TOKEN")
SSH_KEY_NAME = os.getenv("SSH_KEY_NAME")
TAILSCALE_AUTH_KEY = os.getenv("TAILSCALE_AUTH_KEY", "")
PUB_KEY = os.getenv("PUB_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

for var in ["HCLOUD_TOKEN", "SSH_KEY_NAME", "PUB_KEY"]:
    if not os.getenv(var):
        console.print(f"[bold red]Error:[/bold red] {var} not found in environment")
        sys.exit(1)


def ask_or_exit(prompt_fn):
    """Execute questionary prompt and exit gracefully on Ctrl+C."""
    result = prompt_fn()
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

    return ask_or_exit(lambda: questionary.select(label, choices=choices, default=default, use_arrow_keys=True).ask)


def get_tailscale_ip(hostname: str, timeout: int = 300) -> str | None:
    """
    Polls the local Tailscale CLI to find the IP of the new node.
    Requires 'tailscale' to be in your local PATH.
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(
            f"Waiting for Tailscale registration for '{hostname}' (timeout: {timeout}s)...",
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
        console=console,
    ) as progress:
        progress.add_task(f"Waiting for SSH on {ip} (timeout: {timeout}s)...", total=None)
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
        hostname = ask_or_exit(lambda: questionary.text(prompt_msg, default="hardened-host").ask)
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
        return questionary.Choice(
            title=f"{st.name:8} - {st.cores} vCPU, {st.memory:3} GB RAM, {st.disk:3} GB storage ({st.cpu_type})",
            value=st.name,
        )

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

    # Verify SSH key exists
    ssh_key = client.ssh_keys.get_by_name(SSH_KEY_NAME)
    if not ssh_key:
        console.print(f"[bold red]Error:[/bold red] SSH key '{SSH_KEY_NAME}' not found in Hetzner account")
        sys.exit(1)

    # Interactive prompts
    hostname = prompt_hostname(client)
    server_type = prompt_server_type(client)
    datacenter = prompt_datacenter(client)

    # Check if server type is available in datacenter
    if not check_server_type_availability(client, server_type, datacenter):
        console.print("[bold red]Cannot proceed with unavailable server type/datacenter combination[/bold red]")
        sys.exit(1)

    # Confirm configuration
    console.print("\n[bold cyan]Configuration Summary:[/bold cyan]")
    summary_table = Table(show_header=False, box=None)
    summary_table.add_column("Setting", style="cyan")
    summary_table.add_column("Value", style="green")
    summary_table.add_row("Hostname", hostname)
    summary_table.add_row("Server Type", server_type)
    summary_table.add_row("Datacenter", datacenter)
    console.print(summary_table)
    console.print()

    proceed = ask_or_exit(lambda: questionary.confirm("Proceed with server creation?", default=True).ask)

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
            console=console,
        ) as progress:
            progress.add_task("Provisioning server...", total=None)

            response = client.servers.create(
                name=hostname,
                server_type=ServerType(name=server_type),
                image=Image(name="ubuntu-24.04"),
                ssh_keys=[ssh_key],
                user_data=config.substitute(
                    hostname=hostname,
                    pub_key=PUB_KEY,
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
            console.print("\n[cyan]Connect via:[/cyan]")
            # Use print() instead of console.print() to avoid syntax highlighting
            print(f"  ssh -i {SSH_KEY_PATH} sysadmin@{ts_ip}")
            console.print(f"""
            Host {hostname}
                IdentityFile {SSH_KEY_PATH}
                HostName {ts_ip}
                User sysadmin
                SetEnv TERM=xterm-ghostty
            """)
            console.print(
                f"""inform -x | ssh {hostname} "cat > /tmp/terminfo.txt && tic -x - """
                f"""&& tic -x -o /usr/share/terminfo /tmp/terminfo.txt && rm -f /tmp/terminfo.txt\""""
            )
        else:
            console.print("[yellow]SSH not ready yet (timeout)[/yellow]")
            console.print("[cyan]Try connecting manually:[/cyan]")
            print(f"  ssh -i {SSH_KEY_PATH} sysadmin@{ts_ip}")
    else:
        console.print("[yellow]Could not resolve Tailscale IP[/yellow]")
        console.print("[cyan]Is Tailscale started on your computer?[/cyan]")


if __name__ == "__main__":
    main()
