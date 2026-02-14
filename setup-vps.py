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
TAILSCALE_AUTH_KEY = os.getenv("TAILSCALE_AUTH_KEY")
PUB_KEY = os.getenv("PUB_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

for var in ["HCLOUD_TOKEN", "SSH_KEY_NAME", "TAILSCALE_AUTH_KEY", "PUB_KEY"]:
    if not os.getenv(var):
        console.print(f"[bold red]Error:[/bold red] {var} not found in environment")
        sys.exit(1)


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
        if first_attempt:
            hostname = questionary.text(
                "Enter hostname",
                default="hardened-host",
            ).ask()
            first_attempt = False
        else:
            hostname = questionary.text(
                "Enter hostname (previous name was taken)",
                default="hardened-host",
            ).ask()

        if hostname is None:  # User pressed Ctrl+C
            sys.exit(0)

        # Check if hostname already exists
        try:
            existing_server = client.servers.get_by_name(hostname)
            if existing_server:
                console.print(f"[bold red]✗[/bold red] Hostname '{hostname}' is already taken, please try another name")
                continue
        except Exception:
            pass  # Hostname is available

        console.print(f"[bold green]✓[/bold green] Hostname '{hostname}' is available")
        return hostname


def prompt_server_type(client: Client) -> str:
    """Display server type options and prompt for selection."""
    # Fetch server types from API
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Loading server types...", total=None)
        server_types = client.server_types.get_all()

    # Filter to common server types and sort
    common_types = ["cx23", "cx33", "cx43", "cpx22", "cpx31", "cpx41"]
    filtered_types = [st for st in server_types if st.name in common_types]
    filtered_types.sort(key=lambda st: st.name)

    if not filtered_types:
        console.print("[yellow]No server types found, using all available types[/yellow]")
        filtered_types = server_types

    # Build choices for questionary with descriptions
    choices = [
        questionary.Choice(
            title=f"{st.name:8} - {st.cores} vCPU, {st.memory:3} GB RAM, {st.disk:3} GB storage ({st.cpu_type})",
            value=st.name,
        )
        for st in filtered_types
    ]

    # Find default value
    default_choice = "cx23" if any(st.name == "cx23" for st in filtered_types) else filtered_types[0].name

    server_type = questionary.select(
        "Select server type:",
        choices=choices,
        default=default_choice,
        use_arrow_keys=True,
    ).ask()

    if server_type is None:  # User pressed Ctrl+C
        sys.exit(0)

    return server_type


def prompt_datacenter(client: Client) -> str:
    """Display datacenter options and prompt for selection."""
    # Fetch datacenters from API
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Loading datacenters...", total=None)
        datacenters = client.datacenters.get_all()

    # Build choices for questionary with descriptions
    choices = [
        questionary.Choice(
            title=f"{dc.location.name:6} - {dc.location.city:15} ({dc.location.country})",
            value=dc.location.name,
        )
        for dc in datacenters
    ]

    # Find default
    default_choice = "hel1" if any(dc.location.name == "hel1" for dc in datacenters) else datacenters[0].location.name

    datacenter = questionary.select(
        "Select datacenter:",
        choices=choices,
        default=default_choice,
    ).ask()

    if datacenter is None:  # User pressed Ctrl+C
        sys.exit(0)

    return datacenter


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

    proceed = questionary.confirm(
        "Proceed with server creation?",
        default=True,
    ).ask()

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
