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
   # this installs uv if it's not already installed, and sets up the virtual environment
   make install
   ```

2. Secrets are referenced (not stored) in the committed `.op.env` and injected
   lazily by `op run` — `HCLOUD_TOKEN`, `PUB_KEY`, `TAILSCALE_AUTH_KEY`, and the
   optional `GITHUB_TOKEN`. The non-secret `SSH_KEY_NAME` lives in `.envrc`
   (direnv). Run the deploy with the secrets injected for that command only:

   ```bash
   direnv allow                       # loads SSH_KEY_NAME (no secrets)
   op-run -- ./deploy.sh              # or: op run --env-file=.op.env -- ./deploy.sh
   ```

   The pattern: `.op.env` holds only 1Password secret *references* (e.g.
   `op://vault/item/field`), never the secrets themselves, so it's safe to commit;
   `op run` resolves them at runtime for that single command. See the
   [1Password `op run` docs](https://developer.1password.com/docs/cli/secrets-environment-variables/).
   `GITHUB_TOKEN` is optional—omit from `.op.env` if you don't need GitHub CLI /
   Docker GHCR registry pre-authenticated.

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
```

Then: `ssh myserver`

### Ghostty Terminal Support

See https://ghostty.org/docs/help/terminfo. In `~/.config/ghostty/config`, set

```
shell-integration-features = ssh-terminfo,ssh-env
```

Required for proper terminal app display (`htop`, `vim`, etc.).

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

## Files

- `setup-vps.py` - Provisioning script
- `cloud-config.yaml.tmpl` - Cloud-init template
- `.env.example` - Environment variable template
