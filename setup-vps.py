import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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
_STDOUT_PIPED = not sys.stdout.isatty() and sys.stdin.isatty()
_force_color = True if _STDOUT_PIPED else None
console = Console(force_terminal=_force_color)

# Uploaded to Hetzner (it must exist there), but SSH to the box goes over Tailscale SSH,
# which authenticates by tailnet identity and policy, not by this key.
SSH_KEY_PATH = Path("~/.ssh/Hetzner_Automation_Key").expanduser()
SSH_USER = "sysadmin"
TAILSCALE_TAG = "tag:vps"
TAILSCALE_API = "https://api.tailscale.com/api/v2"
# Tailscale SSH rule the tailnet policy needs; the default rule only covers your own (untagged) devices.
TAILSCALE_SSH_RULE = f"""{{
  "action": "accept",
  "src":    ["<your Tailscale login>"],
  "dst":    ["{TAILSCALE_TAG}"],
  "users":  ["{SSH_USER}"]
}}"""
# One DNS label (RFC 1123): Tailscale and MagicDNS use it as the node name.
HOSTNAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")

HCLOUD_TOKEN = os.getenv("HCLOUD_TOKEN")
SSH_KEY_NAME = os.getenv("SSH_KEY_NAME")
TAILSCALE_AUTH_KEY = os.getenv("TAILSCALE_AUTH_KEY", "")
TAILSCALE_OAUTH_CLIENT_ID = os.getenv("TAILSCALE_OAUTH_CLIENT_ID", "")
TAILSCALE_OAUTH_CLIENT_SECRET = os.getenv("TAILSCALE_OAUTH_CLIENT_SECRET", "")
PUB_KEY = os.getenv("PUB_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

for var in ["HCLOUD_TOKEN", "SSH_KEY_NAME"]:
    if not os.getenv(var):
        console.print(f"[bold red]Error:[/bold red] {var} not found in environment")
        sys.exit(1)

USE_TAILSCALE_OAUTH = bool(TAILSCALE_OAUTH_CLIENT_ID and TAILSCALE_OAUTH_CLIENT_SECRET)
if not USE_TAILSCALE_OAUTH and not TAILSCALE_AUTH_KEY:
    console.print(
        "[bold red]Error:[/bold red] set TAILSCALE_OAUTH_CLIENT_ID + TAILSCALE_OAUTH_CLIENT_SECRET (recommended) "
        "or TAILSCALE_AUTH_KEY"
    )
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


def print_ssh_qr(user: str, host: str) -> None:
    """Print a scannable QR encoding an ssh:// URI — any SSH client app can ingest it."""
    qr = qrcode.QRCode(border=1)
    qr.add_data(f"ssh://{user}@{host}")
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def print_connection_info(hostname: str, ip: str) -> None:
    """Show ssh + mosh commands and a phone-scannable QR.

    They use the hostname: MagicDNS resolves it on every tailnet device, and Tailscale SSH needs no key or
    ~/.ssh/config entry. The IP is shown for clients without MagicDNS.
    """
    console.print("\n[bold cyan]Connect:[/bold cyan]")
    # print() (not console.print) to keep the commands copy-paste clean, no markup parsing.
    print(f"  ssh {SSH_USER}@{hostname}")
    print(f"  mosh {SSH_USER}@{hostname}   # roaming-friendly, great from a phone")
    console.print(f"[dim]  Tailscale IP: {ip}[/dim]")

    console.print("\n[bold cyan]Scan to connect from your phone[/bold cyan] (any SSH client):")
    print_ssh_qr(SSH_USER, hostname)
    console.print(f"[dim]Encodes ssh://{SSH_USER}@{hostname}[/dim]")


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


def _tailscale_post(path: str, data: bytes, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(f"{TAILSCALE_API}{path}", data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def mint_tailscale_key(hostname: str) -> str:
    """Create a single-use, pre-authorized, tagged auth key that expires in an hour.

    The key goes into the server's user_data, which the metadata service serves to anything on
    the box for the server's lifetime, so it must be worthless once the node has joined.
    """
    token = _tailscale_post(
        "/oauth/token",
        urllib.parse.urlencode(
            {"client_id": TAILSCALE_OAUTH_CLIENT_ID, "client_secret": TAILSCALE_OAUTH_CLIENT_SECRET}
        ).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )["access_token"]
    create = {"reusable": False, "ephemeral": False, "preauthorized": True, "tags": [TAILSCALE_TAG]}
    body = {
        "capabilities": {"devices": {"create": create}},
        "expirySeconds": 3600,
        "description": f"hetzner-vps-setup {hostname}"[:50],
    }
    return _tailscale_post(
        "/tailnet/-/keys",
        json.dumps(body).encode(),
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )["key"]


def tailscale_auth_key(hostname: str) -> str:
    """Single-use key minted via the OAuth client, or the reusable $TAILSCALE_AUTH_KEY as a fallback."""
    if not USE_TAILSCALE_OAUTH:
        console.print(
            "[yellow]Warning:[/yellow] using the reusable $TAILSCALE_AUTH_KEY — it stays readable on the box "
            "via the metadata service. Set TAILSCALE_OAUTH_CLIENT_ID/SECRET to mint a single-use key instead."
        )
        return TAILSCALE_AUTH_KEY
    try:
        key = mint_tailscale_key(hostname)
    except urllib.error.HTTPError as e:
        console.print(f"[bold red]Failed to create a Tailscale auth key:[/bold red] {e.code} - {e.read().decode()}")
        sys.exit(1)
    except (urllib.error.URLError, KeyError, ValueError) as e:
        console.print(f"[bold red]Failed to create a Tailscale auth key:[/bold red] {e}")
        sys.exit(1)
    console.print("[bold green]✓[/bold green] Created a single-use Tailscale auth key (expires in 1h)")
    return key


def ssh_command(ip: str, remote: str) -> list[str]:
    """ssh argv for running `remote` on the new box; dead connections (e.g. a reboot) fail within ~45s."""
    return [
        "ssh",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
        f"{SSH_USER}@{ip}",
        remote,
    ]


def _local_tailscale_status() -> dict:
    """`tailscale status --json` for this machine, or {} if the daemon can't be reached."""
    try:
        result = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError):
        return {}


def _local_tailscale_state() -> str | None:
    """This machine's Tailscale BackendState (Running, Stopped, NeedsLogin, ...), or None if unreachable."""
    return _local_tailscale_status().get("BackendState")


def forget_old_host_keys(hostname: str, ip: str) -> None:
    """Drop known_hosts entries an earlier box with the same name (or a reused IP) left behind.

    A rebuilt box has a new host key, so ssh/mosh would refuse it with "REMOTE HOST IDENTIFICATION HAS
    CHANGED". Dropping the old key is safe here: the connection runs over Tailscale, which already
    authenticates the node.
    """
    hosts = [hostname, ip]
    if suffix := _local_tailscale_status().get("MagicDNSSuffix"):
        hosts.append(f"{hostname}.{suffix}")
    for host in hosts:
        try:
            result = subprocess.run(["ssh-keygen", "-R", host], capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, subprocess.SubprocessError):
            return
        if "found" in result.stdout:
            console.print(f"[dim]Removed the old host key for {host} from ~/.ssh/known_hosts[/dim]")


def _wait_for_local_tailscale(done, timeout: int = 30) -> str | None:
    """Poll the local BackendState until done(state) is true or the timeout passes; return the last state."""
    deadline = time.time() + timeout
    while not done(state := _local_tailscale_state()) and time.time() < deadline:
        time.sleep(1)
    return state


def ensure_local_tailscale() -> None:
    """Make sure this machine is on the tailnet, starting Tailscale if needed.

    The script finds the new node and SSHes into it over Tailscale; without a running local
    client it would create a paid server and then wait out the registration timeout.
    """
    if not shutil.which("tailscale"):
        console.print(
            "[bold red]Error:[/bold red] the `tailscale` CLI is not installed — https://tailscale.com/download"
        )
        sys.exit(1)

    state = _local_tailscale_state()
    if state is None and sys.platform == "darwin":
        # On macOS the CLI talks to the Tailscale app; it can't answer while the app isn't running.
        console.print("[cyan]Starting the Tailscale app...[/cyan]")
        subprocess.run(["open", "-a", "Tailscale"], capture_output=True, timeout=10)
        state = _wait_for_local_tailscale(lambda s: s not in (None, "NoState", "Starting"))
    if state == "Stopped":
        console.print("[cyan]Tailscale is stopped on this machine — running `tailscale up`...[/cyan]")
        subprocess.run(["tailscale", "up"], capture_output=True, text=True, timeout=30)
        state = _wait_for_local_tailscale(lambda s: s == "Running")

    if state != "Running":
        hints = {
            None: "can't reach the Tailscale daemon (Linux: `sudo systemctl start tailscaled`)",
            "NeedsLogin": "it needs a login — run `tailscale up` or sign in from the Tailscale app",
        }
        console.print(
            f"[bold red]Error:[/bold red] Tailscale on this machine is not running: {hints.get(state, state)}"
        )
        sys.exit(1)
    console.print("[bold green]✓[/bold green] Tailscale is running on this machine")


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
            f"Step 2/4 · Waiting for Tailscale registration for '{hostname}' (timeout: {timeout}s)...",
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
        progress.add_task(f"Step 3/4 · Waiting for SSH on {ip} (timeout: {timeout}s)...", total=None)
        start = time.time()
        policy_hint_shown = False
        while time.time() - start < timeout:
            try:
                result = subprocess.run(ssh_command(ip, "echo ready"), capture_output=True, text=True, timeout=10)
            except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                result = None
            if result and result.returncode == 0:
                return True
            if result and "tailnet policy does not permit" in result.stderr and not policy_hint_shown:
                # Keep polling: the user can fix the policy while we wait.
                console.print(
                    "[bold red]✗[/bold red] Tailscale SSH is refused by your tailnet policy. Add this rule under "
                    "Access controls → Tailscale SSH (https://login.tailscale.com/admin/acls), then wait here:"
                )
                print(TAILSCALE_SSH_RULE)
                policy_hint_shown = True
            time.sleep(5)
    return False


def wait_for_cloud_init(ip: str, timeout: int = 900) -> int | None:
    """Wait for cloud-init to finish, including the reboot at the end of runcmd.

    Returns `cloud-init status` exit code (0 done, 2 done with recoverable errors, 1 failed), or None on
    timeout. The reboot drops the connection mid-`--wait` (ssh exits 255), so retry until the second boot answers.
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        progress.add_task(f"Step 4/4 · Waiting for cloud-init to finish (timeout: {timeout}s)...", total=None)
        deadline = time.time() + timeout
        while (remaining := deadline - time.time()) > 0:
            try:
                result = subprocess.run(
                    ssh_command(ip, "cloud-init status --wait"), capture_output=True, timeout=remaining
                )
            except subprocess.TimeoutExpired:
                return None
            if result.returncode in (0, 1, 2):
                return result.returncode
            time.sleep(5)
    return None


def push_github_token(ip: str) -> None:
    """Log gh and Docker (GHCR) in on the box over SSH, so the token never enters user_data."""
    script = (
        'read -r t; printf %s "$t" | gh auth login --with-token'
        ' && printf %s "$t" | docker login ghcr.io -u nobody --password-stdin'
    )
    try:
        result = subprocess.run(
            ssh_command(ip, f"sh -c {shlex.quote(script)}"),
            input=f"{GITHUB_TOKEN}\n",
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        console.print("[yellow]Warning:[/yellow] timed out logging gh / GHCR in on the box")
        return
    if result.returncode == 0:
        console.print("[bold green]✓[/bold green] gh CLI and GHCR logged in on the box")
    else:
        console.print(f"[yellow]Warning:[/yellow] gh / GHCR login on the box failed: {result.stderr.strip()}")


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
    if _STDOUT_PIPED:
        # questionary (prompt_toolkit) can't read the terminal size or cursor position through a pipe:
        # it assumes 80 columns and redraws prompts at the wrong place.
        console.print(
            "[yellow]Warning:[/yellow] stdout is piped (e.g. `op run` masking), so the prompts will render garbled. "
            "Run with `op-run --no-masking --` / `op run --no-masking --`."
        )

    # Before any prompt: the hostname check and everything after server creation go through the tailnet.
    ensure_local_tailscale()

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
    summary_table.add_row(
        "Tailscale key", "single-use (OAuth)" if USE_TAILSCALE_OAUTH else "reusable $TAILSCALE_AUTH_KEY"
    )
    console.print(Panel(summary_table, title="[bold cyan]Configuration Summary[/bold cyan]", border_style="cyan"))
    console.print()

    proceed = ask_or_exit(questionary.confirm("Proceed with server creation?", default=True))

    if not proceed:
        console.print("[yellow]Cancelled by user[/yellow]")
        sys.exit(0)

    # Load cloud-init configuration
    config = Template((Path(__file__).parent / "cloud-config.yaml.tmpl").read_text())
    tailscale_key = tailscale_auth_key(hostname)

    # Create server
    console.print(f"\n[bold cyan]Creating server: {hostname}[/bold cyan]")

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            progress.add_task("Step 1/4 · Provisioning server...", total=None)

            response = client.servers.create(
                name=hostname,
                server_type=ServerType(name=server_type.name),
                image=Image(name="ubuntu-24.04"),
                ssh_keys=[ssh_key],
                user_data=config.substitute(
                    hostname=hostname,
                    pub_key=pub_key_text,
                    tailscale_key=tailscale_key,
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
        forget_old_host_keys(hostname, ts_ip)
        console.print("[cyan]Waiting for server to complete reboot and allow VPN SSH...[/cyan]")

        # Wait for SSH on that IP
        ssh_ready = wait_for_ssh(ts_ip)

        if ssh_ready:
            status = wait_for_cloud_init(ts_ip)
            if status is None:
                console.print(
                    "[yellow]cloud-init is still running (timeout) — check `cloud-init status --long`[/yellow]"
                )
            elif status != 0:
                console.print(
                    "[yellow]cloud-init finished with errors — check `sudo cloud-init status --long` "
                    "and /var/log/cloud-init-output.log[/yellow]"
                )
            if status is not None and GITHUB_TOKEN:
                push_github_token(ts_ip)
            console.print(f"\n[bold green]✓ SSH ready on host {hostname} / {ts_ip}![/bold green]")
            print_connection_info(hostname, ts_ip)
        else:
            console.print("[yellow]SSH not ready yet (timeout)[/yellow]")
            console.print("[cyan]Try connecting manually:[/cyan]")
            print(f"  ssh {SSH_USER}@{hostname}")
    else:
        console.print("[yellow]Could not resolve Tailscale IP[/yellow]")
        console.print("[cyan]Is Tailscale started on your computer?[/cyan]")


if __name__ == "__main__":
    main()
