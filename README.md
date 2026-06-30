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

5. **Tailscale Auth Key** (reusable, tagged):
    - First, define the tag owner once in your ACL at
      [login.tailscale.com/admin/acls](https://login.tailscale.com/admin/acls):
      ```json
      "tagOwners": { "tag:vps": ["autogroup:admin"] }
      ```
    - Create the key at [login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys)
    - Enable **Reusable**; under **Tags**, select `tag:vps` (leave *Ephemeral* off —
      these are persistent boxes). Tagged nodes get ACL scoping and don't hit the
      180-day key-expiry re-auth.

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

   See `~/Developer/private/dotfiles/docs/secrets-management.md` for the pattern.
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

At the end of provisioning the script offers to **append the `Host` block to your
`~/.ssh/config`** automatically, so you can just:

```
ssh <hostname>
```

(It skips silently if a matching `Host` entry already exists.) The block it adds:

```
Host <hostname>
  HostName <tailscale-ip>
  User sysadmin
  IdentityFile ~/.ssh/Hetzner_Automation_Key
```

### From your phone (Mosh + QR)

The provisioner prints a **QR code** encoding `ssh://sysadmin@<tailscale-ip>` — scan
it with any SSH client (Termius, Blink, …) to connect; the QR is app-agnostic, it's
just a standard SSH URI.

For a connection that survives network changes and sleep (ideal on mobile), use
**Mosh** (pre-installed):

```bash
mosh --ssh="ssh -i ~/.ssh/Hetzner_Automation_Key" sysadmin@<tailscale-ip>
```

Mosh rides the Tailscale tunnel (no public ports are opened — UFW already allows all
traffic on `tailscale0`).

### Web preview (`publish`)

To share a locally-running dev server without opening any inbound port, run on the
box:

```bash
publish 3000        # → ephemeral public https://<random>.trycloudflare.com URL
```

This opens an **outbound** Cloudflare quick tunnel (`cloudflared`) to
`localhost:3000`. Ctrl+C to stop. The URL is random and ephemeral; a stable named
URL would need a Cloudflare account (see `ROADMAP.md`).

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
- **Remote/mobile**: mosh (roaming SSH), Tailscale SSH
- **Monitoring**: htop, iotop, ncdu
- **Docker**: docker.io, docker-compose-v2
- **Security**: ufw (firewall), unattended-upgrades
- **VPN**: Tailscale (with SSH enabled, node tagged `tag:vps`)
- **Dev**: Claude Code, `cloudflared` + the `publish <port>` helper

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
