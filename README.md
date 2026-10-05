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
3. **`TAILSCALE_OAUTH_CLIENT_ID` + `TAILSCALE_OAUTH_CLIENT_SECRET`** — a Tailscale OAuth
   client that `setup-vps.py` uses to mint a **single-use, 1-hour** auth key for each new
   server. One-time setup:
   - Define the tag owner once in your ACL
     ([login.tailscale.com/admin/acls](https://login.tailscale.com/admin/acls)):
     ```json
     "tagOwners": { "tag:vps": ["autogroup:admin"] }
     ```
   - Create the OAuth client: admin console → **Settings → Trust credentials** →
     **Credential** → **OAuth**; scope **Auth Keys → Write**, tag `tag:vps`. The secret
     is shown only once. Tagged nodes get ACL scoping and skip
     the 180-day key-expiry re-auth; the nodes are persistent (not ephemeral).
   - Allow yourself to SSH in. The servers run **Tailscale SSH**, which authenticates by
     tailnet identity and policy, not SSH keys, and Tailscale's default SSH rule only
     covers your own untagged devices. Under Access controls → **Tailscale SSH** →
     **Add rule** (or in the policy JSON's `"ssh"` array):
     ```json
     {
       "action": "accept",
       "src":    ["you@example.com"],
       "dst":    ["tag:vps"],
       "users":  ["sysadmin"]
     }
     ```
     Use your own login rather than `autogroup:member` if others are in your tailnet —
     `sysadmin` has passwordless sudo. `accept`, not `check`: check mode needs a browser
     re-auth that the provisioner's non-interactive SSH can't do. Without this rule the
     provisioner reports the refusal at step 3.
   - **Why not a plain auth key:** the key is written into the server's cloud-init
     user_data, which Hetzner's metadata service (`169.254.169.254`) serves to any process
     on the box for its whole lifetime. A reusable key read from there lets anyone join
     devices to your tailnet as `tag:vps`; a single-use key is already spent.
   - **Fallback:** `TAILSCALE_AUTH_KEY` (reusable, `tag:vps`, non-ephemeral, from
     [admin/settings/keys](https://login.tailscale.com/admin/settings/keys)) still works
     if the OAuth client is unset; `doctor` and `setup-vps.py` warn about it.

`GITHUB_TOKEN` is optional (gh CLI / GHCR login on the box). It is sent over SSH once
the server is up and never written into user_data.

> **Advanced (maintainer's setup):** instead of a `.env`, secrets can be injected at
> point-of-use from 1Password via `op-run` (a personal wrapper) reading the committed
> `.op.env`. That file holds only 1Password *references* (`op://vault/item/field`),
> never the secrets themselves, so it's safe to commit; `op run` resolves them at
> runtime for that single command (see the
> [1Password `op run` docs](https://developer.1password.com/docs/cli/secrets-environment-variables/)).
> This is personal and optional — if you don't already use that workflow, the `.env`
> path above is all you need. Run any command with secrets injected as
> `op-run -- <cmd>` (e.g. `op-run -- make doctor`). Provision with
> `op-run --no-masking -- uv run setup-vps.py`: masking pipes stdout, and the interactive
> prompts can't size or position themselves through a pipe, so they render garbled.
> The script prints no secrets.

## Provision

```bash
make doctor             # preflight: tools, credentials, valid token, SSH key
uv run setup-vps.py
```

`doctor` reports exactly what's missing for anything not ready. The provisioner then
prompts for:

- **Hostname** (default: `hardened-host`)
- **Location** (default: `hel1`; only locations with orderable server types are offered)
- **Server type** (only types available at that location right now, cheapest first, with monthly cost)

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
```

No `IdentityFile`: Tailscale SSH lets you in by tailnet identity, not by key.

### From your phone (Mosh + QR)

The provisioner prints a **QR code** encoding `ssh://sysadmin@<tailscale-ip>` — scan
it with any SSH client (Termius, Blink, …) to connect; the QR is app-agnostic, it's
just a standard SSH URI.

For a connection that survives network changes and sleep (ideal on mobile), use
**Mosh** (pre-installed):

```bash
mosh sysadmin@<tailscale-ip>
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

Ubuntu 24.04's ncurses predates the `xterm-ghostty` entry, so without this `htop`, `vim`,
`tmux` etc. fail with "missing or unsuitable terminal". On the first `ssh` from a Ghostty
shell it installs the entry into `~/.terminfo` on the server (no root needed), and falls
back to `xterm-256color` if that fails. mosh sets its own `TERM` and needs nothing.

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
- **Dev**: Claude Code, herdr (agent multiplexer, Claude integration pre-installed), `cloudflared` + the `publish <port>` helper

### Shell Features

- **History**: 50k commands in memory, 100k on disk, timestamped
- **fzf shortcuts**:
    - `Ctrl+R` - Fuzzy command history search
    - `Ctrl+T` - File finder
    - `Alt+C` - Directory finder

### Security

- **UFW**: Only 41641/udp (Tailscale) open publicly
- **Metadata service**: blocked for Docker containers (`DOCKER-USER` rule)
- **Docker ports**: `-p` publishes to `127.0.0.1` by default (Docker bypasses UFW); bind a
  host IP explicitly to expose one, e.g. `-p <tailscale-ip>:8080:80`
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
