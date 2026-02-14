# Hetzner VPS Setup

Automated provisioning of hardened Ubuntu VPS servers on Hetzner Cloud with Tailscale VPN, Docker, and developer tools.

## Prerequisites

1. **uv** (Python package manager):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   # Or via Homebrew:
   brew install uv
   ```

2. **Tailscale CLI** (for VPN IP resolution):
   ```bash
   brew install tailscale
   open -a Tailscale  # Authenticate
   tailscale status   # Verify
   ```

3. **Hetzner API Token** (Read & Write):
   - Create at [console.hetzner.cloud](https://console.hetzner.cloud/) → Security → API Tokens
   - Save to 1Password

4. **SSH Key**:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/Hetzner_Automation_Key -C "hetzner-automation"
   ```
   - Upload public key to [Hetzner Console](https://console.hetzner.cloud/) → Security → SSH Keys
   - Name it: `Hetzner Automation Key` (exact name required)

5. **Tailscale Auth Key** (reusable):
   - Create at [login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys)
   - Enable "Reusable" option

## Setup

1. Install dependencies:

```bash
make install
```

2. Copy `.env.example` to `.env` and configure:

   ```bash
   cp .env.example .env
   ```

   Edit `.env`:
   ```bash
   export HCLOUD_TOKEN=$(op read "op://Private/your-hetzner-token/credential")
   export SSH_KEY_NAME=Hetzner Automation Key
   export PUB_KEY=$(op read "op://Private/Hetzner Automation Key/public key")
   export TAILSCALE_AUTH_KEY=$(op read "op://Private/Tailscale Hetzner Auth Key/credential")
   export GITHUB_TOKEN=$(op read "op://Private/github-token/credential")  # Optional: for gh CLI auto-login
   ```

   **Note**: Uses 1Password CLI (`op`). Replace with direct values if needed. `GITHUB_TOKEN` is optional—omit if you don't need GitHub CLI pre-authenticated.

## Usage

```bash
source .env
uv run setup-vps.py
```

Interactive prompts:
- **Hostname** (default: `hardened-host`)
- **Server type** (default: `cpx22`)
- **Datacenter** (default: `hel1`)

Outputs Tailscale VPN IP for SSH access.

## Connect

```bash
ssh sysadmin@<tailscale-ip>
```

**Recommended**: Add to `~/.ssh/config`:
```
Host myserver
  HostName <tailscale-ip>
  User sysadmin
  IdentityFile ~/.ssh/Hetzner_Automation_Key
  SetEnv TERM=xterm-ghostty
```

Then: `ssh myserver`

### Ghostty Terminal Support

First connection only:
```bash
infocmp -x | ssh myserver "cat > /tmp/terminfo.txt && tic -x - && tic -x -o /usr/share/terminfo /tmp/terminfo.txt && rm -f /tmp/terminfo.txt"
```

Required for terminal apps (`htop`, `vim`, etc.) and sudo commands.

## What's Installed

### System
- **OS**: Ubuntu 24.04 LTS
- **User**: `sysadmin` (passwordless sudo, docker group)
- **Timezone**: Europe/Berlin
- **Swap**: 2GB

### Packages
- **Tools**: git, curl, wget, jq, vim, tmux, ripgrep, fzf, gh
- **Monitoring**: htop, iotop, ncdu
- **Docker**: docker.io, docker-compose-v2
- **Security**: ufw (firewall), unattended-upgrades
- **VPN**: Tailscale (with SSH enabled)

### Shell Features
- **History**: 50k commands in memory, 100k on disk, timestamped
- **fzf shortcuts**:
  - `Ctrl+R` - Fuzzy command history search
  - `Ctrl+T` - File finder
  - `Alt+C` - Directory finder

### Security
- **UFW**: Only ports 443/tcp and 41641/udp (Tailscale) open
- **Tailscale SSH**: VPN-only access, no public SSH
- **Auto-updates**: Security patches via unattended-upgrades
- **Docker logs**: Auto-rotation (3 × 10MB max)

## Troubleshooting

**SSH key not found**: Key name in Hetzner must match `SSH_KEY_NAME` exactly.

**Hostname already taken**: Choose different name (unique per Hetzner account).

**Tailscale IP not found**: Ensure `tailscale status` shows your tailnet is active.

**Terminal errors** (`Error opening terminal: xterm-ghostty`): Run the terminfo install command above.

## Files

- `setup-vps.py` - Provisioning script
- `cloud-config.yaml.tmpl` - Cloud-init template
- `.env.example` - Environment variable template
