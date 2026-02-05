# Hetzner VPS Setup

Automated provisioning of hardened Hetzner VPS servers with Tailscale VPN, Docker, and security hardening.

## First-Time Setup

### 1. Prerequisites

Install required tools:

```bash
brew install tailscale
```

**Start Tailscale** and authenticate with your tailnet:

```bash
# Open Tailscale app to log in
open -a Tailscale

# Verify it's running
tailscale status
```

The script requires a working Tailscale connection to resolve VPN IPs for your new servers.

### 2. Create Hetzner Cloud API Token

1. Go to [Hetzner Cloud Console](https://console.hetzner.cloud/)
2. Create a new project (or select existing)
3. Navigate to **Security** → **API Tokens**
4. Click **Generate API Token**
5. Give it a name (e.g., "hetzner-automation") and **Read & Write** permissions
6. **Save the token immediately to 1Password** (you won't see it again)

### 3. Create SSH Key Pair

Generate a dedicated SSH key for Hetzner automation:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/Hetzner_Automation_Key -C "hetzner-automation"
```

Upload the **public key** to Hetzner:

1. Copy the public key to clipboard:
   ```bash
   cat ~/.ssh/Hetzner_Automation_Key.pub | pbcopy
   ```

2. Go to [Hetzner Cloud Console](https://console.hetzner.cloud/)
3. Navigate to **Security** → **SSH Keys**
4. Click **Add SSH Key**
5. Name it: `Hetzner Automation Key` (exact name matters!)
6. Paste the key (Cmd+V)

### 4. Create Tailscale Auth Key

1. Go to [Tailscale Admin Console](https://login.tailscale.com/admin/settings/keys)
2. Click **Generate auth key**
3. Settings:
    - ✅ **Reusable** (so you can provision multiple servers)
    - ✅ **Ephemeral** (optional - auto-cleanup when server is deleted)
    - Description: "Hetzner Servers"
4. **Copy the key** (starts with `tskey-auth-...`)

### 5. Configure Environment Variables

Copy the example file:

```bash
cd hetzner-setup
cp .env.example .env
```

Edit `.env` with your values:

```bash
# Option A: Store secrets in 1Password (recommended)
HCLOUD_TOKEN=$(op read --account my.1password.com 'op://Private/Hetzner API Token/credential')
SSH_KEY_NAME=Hetzner Automation Key
PUB_KEY=$(op read --account my.1password.com 'op://Private/Hetzner Automation Key/public key')
TAILSCALE_AUTH_KEY=$(op read --account my.1password.com 'op://Private/Tailscale Auth Key/credential')

# Option B: Hardcode secrets (less secure, don't commit!)
HCLOUD_TOKEN=your-hetzner-token-here
SSH_KEY_NAME=Hetzner Automation Key
PUB_KEY=ssh-ed25519 AAAA... your-email@example.com
TAILSCALE_AUTH_KEY=tskey-auth-...
```

## Usage

### Create a Server

Run the interactive setup:

```bash
uv run hetzner-setup/setup-vps.py
```

You'll be prompted for:

1. **Hostname** (default: `hardened-host`)
2. **Server type** (arrow keys to select, default: `cpx22`)
3. **Datacenter** (arrow keys to select, default: `hel1` - Helsinki)

The script will:

- ✅ Check hostname availability
- ✅ Validate server type is available in datacenter
- ✅ Create server with Ubuntu 24.04
- ✅ Install & configure: Docker, Tailscale, UFW, fail2ban
- ✅ Wait for server to be ready
- ✅ Display connection command

### Connect to Server

Use the Tailscale VPN IP (100.x.x.x):

```bash
ssh -i $HOME/.ssh/Hetzner_Automation_Key sysadmin@100.x.x.x
```

## What Gets Installed

### User Account
- Username: `sysadmin`
- Groups: `users`, `admin`, `docker`
- Passwordless sudo enabled
- SSH key authentication only (no password)

### Security
- **UFW firewall**: Only port 443/tcp and Tailscale (41641/udp) open from internet
  - SSH only accessible via Tailscale network (more secure than public SSH)
  - All traffic allowed on `tailscale0` interface
- **fail2ban**: Auto-bans suspicious SSH activity
- **Automatic security updates**: Via `unattended-upgrades`
- **Tailscale SSH**: Replaces traditional SSH, uses your tailnet authentication

### Software
- **Docker** (`docker.io`) + docker-compose-v2
  - Logging limits: max 10MB per file, 3 files rotation
  - Weekly cleanup cron: removes images/containers older than 7 days
- **Tailscale VPN**: Connected to your tailnet with `--ssh --accept-routes`
- **Utilities**: `ncdu` (disk usage analyzer), `git`

### Directory Structure
- `/opt/helpme/` - Main application directory (owned by sysadmin)
  - `helpme-infra/` - Infrastructure code
  - `config/dev/` - Development configs
  - `backups/` - Backup storage
  - `scripts/` - Utility scripts

## Troubleshooting

### "SSH_KEY_NAME not found in Hetzner account"

The SSH key name in Hetzner must **exactly match** the `SSH_KEY_NAME` environment variable. Default:
`Hetzner Automation Key`

Check your SSH keys: [Hetzner Cloud Console → Security → SSH Keys](https://console.hetzner.cloud/)

### "Hostname already taken"

Server names are globally unique in your Hetzner account. Choose a different name.

### SSH connection fails

Wait 2-3 minutes for cloud-init to complete. Check server status in [Hetzner Console](https://console.hetzner.cloud/).

### Tailscale IP not found

Ensure Tailscale is running locally:

```bash
tailscale status
```

The server will still be accessible via public IP shown during creation.

## Files

- `setup-vps.py` - Interactive server provisioning
- `cloud-config.yaml.tmpl` - Cloud-init template for server hardening
- `.env.example` - Example environment variables (copy to `.env`)
