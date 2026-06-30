# Hetzner VPS Setup

Automated provisioning of hardened Ubuntu VPS servers on Hetzner Cloud with Tailscale VPN, Docker, and developer tools.

## Quick start

```bash
brew bundle             # installs uv + tailscale (or install them yourself)
open -a Tailscale       # sign in to your tailnet
make install            # set up the Python environment
cp .env.example .env    # then fill in your Hetzner + Tailscale credentials
make doctor             # verify everything is ready
uv run setup-vps.py     # provision
```

First time through, read **What you need** below for the accounts, credentials, and
the one-time Tailscale tag setup. `make doctor` will tell you exactly what's missing.

## What you need

### Accounts

- **Hetzner Cloud** — [console.hetzner.cloud](https://console.hetzner.cloud/)
- **Tailscale** — [login.tailscale.com](https://login.tailscale.com/) (free for personal use)

### Local tools

`brew bundle` installs both, or install manually:

- **uv** — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Tailscale CLI** — `brew install tailscale`, then `open -a Tailscale` to sign in
  and `tailscale status` to verify.

### Credentials → `.env`

Copy the template and fill it in — `.env` is gitignored and loaded automatically
(no need to `source` it):

```bash
cp .env.example .env
```

1. **`HCLOUD_TOKEN`** — Hetzner API token (Read & Write):
   [console.hetzner.cloud](https://console.hetzner.cloud/) → Security → API Tokens.
2. **`SSH_KEY_NAME`** — the name your SSH key has (or will have) in Hetzner; default
   `Hetzner Automation Key`. **You don't have to pre-create the key:** if none by that
   name exists, `setup-vps.py` offers to upload `$PUB_KEY`, an existing
   `~/.ssh/Hetzner_Automation_Key.pub`, or to generate a fresh keypair for you.
   - **`PUB_KEY`** (optional) — public-key text to upload if the key must be created.
3. **`TAILSCALE_AUTH_KEY`** — reusable, tagged `tag:vps`, non-ephemeral. One-time setup:
   - Define the tag owner once in your ACL
     ([login.tailscale.com/admin/acls](https://login.tailscale.com/admin/acls)):
     ```json
     "tagOwners": { "tag:vps": ["autogroup:admin"] }
     ```
   - Generate the key
     ([login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys)):
     **Reusable** on, **Tags** → `tag:vps`, **Ephemeral** off. Tagged nodes get ACL
     scoping and skip the 180-day key-expiry re-auth.

`GITHUB_TOKEN` is optional (gh CLI / GHCR auto-login on the box).

> **Advanced (maintainer's setup):** instead of a `.env`, secrets can be injected at
> point-of-use from 1Password via `op-run` (a personal wrapper) reading the committed
> `.op.env`. That file holds only 1Password *references* (`op://vault/item/field`),
> never the secrets themselves, so it's safe to commit; `op run` resolves them at
> runtime for that single command (see the
> [1Password `op run` docs](https://developer.1password.com/docs/cli/secrets-environment-variables/)).
> This is personal and optional — if you don't already use that workflow, the `.env`
> path above is all you need. Run any command with secrets injected as
> `op-run -- <cmd>` (e.g. `op-run -- make doctor`, `op-run -- uv run setup-vps.py`).

## Provision

```bash
make doctor             # preflight: tools, credentials, valid token, SSH key
uv run setup-vps.py
```

`doctor` reports exactly what's missing for anything not ready. The provisioner then
prompts for:

- **Hostname** (default: `hardened-host`)
- **Server type** (default: `cx23`, with estimated monthly cost shown)
- **Datacenter** (default: `hel1`)

and prints the Tailscale VPN IP for SSH access when done.

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

**SSH key**: if no Hetzner key matches `SSH_KEY_NAME`, the script offers to create &
upload one. If you already have a key under a *different* name, set `SSH_KEY_NAME` to
match it exactly (names are case-sensitive).

**Hostname already taken**: Choose different name (unique per Hetzner account).

**Tailscale IP not found**: Ensure `tailscale status` shows your tailnet is active.

## Files

- `setup-vps.py` - Provisioning script
- `doctor.py` - Preflight check (`make doctor`)
- `cloud-config.yaml.tmpl` - Cloud-init template
- `Brewfile` - Local tools (`brew bundle`)
- `.env.example` - Environment variable template (copy to `.env`)
- `.op.env` - Maintainer's 1Password references (optional; ignore if not using `op-run`)
