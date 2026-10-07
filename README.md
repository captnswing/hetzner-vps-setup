# Hetzner VPS Setup

One command that creates a hardened Ubuntu server on [Hetzner Cloud](https://www.hetzner.com/cloud),
reachable only through your private [Tailscale](https://tailscale.com/) network, with Docker,
Claude Code and other developer tools ready to use.

- **Private by default:** the server has no public SSH. You reach it over Tailscale, from your
  laptop or your phone.
- **A few minutes per server**, after a one-time setup of about 20 minutes.
- **Costs** what Hetzner charges for the server: roughly €7–25/month for the small types.
  Delete it when you're done and the charges stop.

Works from **macOS or Linux**.

## One-time setup

You need a Hetzner account and a Tailscale account. Tailscale is free for personal use.

### 1. Get the code

```bash
git clone https://github.com/captnswing/hetzner-vps-setup.git
cd hetzner-vps-setup
```

### 2. Install the tools

**macOS** (with [Homebrew](https://brew.sh/)):

```bash
brew bundle            # installs uv and the Tailscale app
```

**Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # uv (runs the Python script)
curl -fsSL https://tailscale.com/install.sh | sh   # Tailscale
```

Then, on either:

```bash
make install
cp .env.example .env
```

You'll fill in `.env` over the next steps. It is gitignored, so your tokens are never committed.

### 3. Hetzner: create an API token

1. Sign in at [console.hetzner.cloud](https://console.hetzner.cloud/) and open (or create) a
   **project**. Servers are created in the project the token belongs to.
2. In the project, go to **Security → API tokens → Generate API token**, with
   **Read & Write** permission.
3. Put it in `.env`:
   ```
   HCLOUD_TOKEN=...
   ```

### 4. Tailscale: join your computer, allow the servers, create an OAuth client

**a. Put your computer on your tailnet.** On macOS, open the Tailscale app and sign in. On
Linux, run `sudo tailscale up`. Check with `tailscale status`.

**b. Edit your tailnet policy.** Go to
[login.tailscale.com/admin/acls](https://login.tailscale.com/admin/acls), switch to the
**JSON editor**, and add two things:

- A `tag:vps` tag for the servers. Add to `"tagOwners"`, creating the section if it doesn't
  exist:
  ```json
  "tagOwners": {
    "tag:vps": ["autogroup:admin"]
  },
  ```
- Permission for **you** to SSH into those servers. Add this rule to the `"ssh"` list,
  next to the rule that is already there, with your own Tailscale login (the email shown
  at the top right of the admin console):
  ```json
  {
    "action": "accept",
    "src":    ["you@example.com"],
    "dst":    ["tag:vps"],
    "users":  ["sysadmin"]
  }
  ```

Save. Tailscale checks the JSON and tells you if a comma is missing.

> Why your login and not everyone: if other people are in your tailnet, `autogroup:member`
> would let them into your servers as `sysadmin`, which has passwordless sudo. Use
> `"accept"`, not `"check"`: check mode asks for a browser login that the script can't do.

**c. Create an OAuth client.** In the admin console, go to **Settings → Trust credentials →
Credential → OAuth**:

- Scope: **Auth Keys → Write**, nothing else.
- Tags: **`tag:vps`**. This only works after step b, because the tag must exist.
- Copy the client ID and secret into `.env` straight away. The secret is shown only once.
  ```
  TAILSCALE_OAUTH_CLIENT_ID=...
  TAILSCALE_OAUTH_CLIENT_SECRET=...
  ```

The script uses this client to create a **single-use key** for each new server, valid for
one hour. Once the server has joined, the key is useless, even though it stays readable
on the server.

### 5. Optional: GitHub token

If you want `gh` and GitHub's container registry (`ghcr.io`) logged in on the server, create a
**classic** token at [github.com/settings/tokens](https://github.com/settings/tokens)
(Tokens (classic) → Generate new token) with scopes `repo`, `read:org` and `read:packages`.
Put it in `.env` as `GITHUB_TOKEN=...`. Leave it blank to skip.

### 6. Check everything

```bash
make doctor
```

It checks the tools, each value in `.env`, the Hetzner token and the Tailscale OAuth
client, and says exactly what to fix. It can't check the policy rules from step 4b.

## Create a server

```bash
make provision
```

It asks for:

- **Hostname** (default `hardened-host`). It must be new in both your Hetzner project and
  your tailnet.
- **Location** (default `hel1`, Helsinki). Only locations where a server can be ordered
  right now are offered.
- **Server type**, cheapest first, with the monthly price. The list includes ARM
  types (shown as `arm`); they work too.

It shows a summary and waits for your confirmation before creating anything. It then
waits for the server to join your tailnet and finish its setup (a few minutes, a bit longer
for ARM), and prints how to connect.

On first run, if your Hetzner project has no SSH key named `Hetzner Automation Key`, the
script offers to create one in `~/.ssh/Hetzner_Automation_Key` and upload it. Hetzner puts
it on the server's root account (without one, it would email you a root password). You won't
need it to connect, because Tailscale handles login.

## Connect

```bash
ssh sysadmin@<hostname>
mosh sysadmin@<hostname>     # survives network changes and sleep; good on a phone
```

Tailscale's MagicDNS resolves the hostname from any device on your tailnet, and Tailscale
SSH logs you in by your Tailscale identity, so there is no key or `~/.ssh/config` entry to
manage. To type just `ssh <hostname>`, add this once to `~/.ssh/config`:

```
Host hardened-*
  User sysadmin
```

From a phone: install Tailscale and an SSH app (Termius, Blink, …), then scan the QR code the
script prints. It encodes `ssh://sysadmin@<hostname>`.

On the server:

- **Claude Code:** run `claude` and log in the first time.
- **Share a dev server:** `publish 3000` gives `localhost:3000` a temporary public
  `https://….trycloudflare.com` URL through an outbound Cloudflare tunnel, without opening
  any port. Ctrl+C stops it.
- **Ghostty users:** add `shell-integration-features = ssh-terminfo,ssh-env` to your Ghostty
  config. Otherwise `htop`, `vim` and `tmux` complain about an unknown terminal, because
  Ubuntu 24.04 doesn't know Ghostty yet.

## Delete a server

1. In the Hetzner console, open the server → **Delete**. Or, with the
   [`hcloud` CLI](https://github.com/hetznercloud/cli): `hcloud server delete <hostname>`.
   Charges stop once it's deleted.
2. Remove it from your tailnet:
   [login.tailscale.com/admin/machines](https://login.tailscale.com/admin/machines) →
   the server → **⋯ → Remove**. Until you do, the script won't reuse the name.

## What's on the server

- **Ubuntu 24.04**, user `sysadmin` with passwordless sudo and Docker access, timezone
  Europe/Berlin, 2 GB swap.
- **Tools:** git, gh, curl, wget, jq, vim, tmux, ripgrep, fd, fzf, mosh, htop, iotop, ncdu,
  build-essential.
- **Docker** with docker-compose, log rotation (3 × 10 MB) and a weekly cleanup of unused
  images.
- **Claude Code** and **herdr** (a terminal multiplexer for coding agents, already wired to
  Claude Code), plus `cloudflared` for `publish`.
- **Shell:** large timestamped history, fzf on Ctrl+R (history), Ctrl+T (files) and Alt+C
  (folders).

## Security

- **No public SSH.** The firewall (UFW) allows only Tailscale's own port (41641/udp) from the
  internet; everything else arrives over the `tailscale0` interface.
- **Docker ports stay private.** `-p 8080:80` binds to `127.0.0.1`, because Docker's own
  firewall rules would otherwise bypass UFW. To expose a port on your tailnet, bind the
  server's Tailscale IP: `-p <tailscale-ip>:8080:80`.
- **No reusable secrets on the server.** Hetzner serves a server's setup data
  (cloud-init "user data") to any process on it for its whole lifetime. So the Tailscale
  key in it is single-use, the GitHub token is sent over SSH instead, and containers are
  blocked from the metadata address (`169.254.169.254`).
- **Automatic security updates** via unattended-upgrades.

## Troubleshooting

**`make doctor` fails** — each failing line says what to fix. Run it again until it passes.

**"Tailscale SSH is refused by your tailnet policy"** (step 3 of a run) — the SSH rule from
setup step 4b is missing or has the wrong login. Add it; the script keeps waiting and
continues once SSH works.

**"is already a node in your tailnet"** — a deleted server with that name is still in your
tailnet. Remove it in the admin console (see [Delete a server](#delete-a-server)) or pick
another name.

**Tailscale isn't running on your computer** — on macOS the script starts it for you,
launching the app if needed. On Linux, run `sudo tailscale up` yourself. Either way, if
Tailscale isn't running or needs a login, the script stops before creating anything.

**The prompts look garbled** — your terminal's output is being piped, for example through a
secrets manager that masks output. See below.

**A run stopped halfway** — a server that was already created keeps running (and costing
money). Delete it as described above, or connect to it and check
`sudo cloud-init status --long`.

## Using a secrets manager instead of `.env`

The scripts read plain environment variables, so any tool that injects them works instead of
a `.env` file. With 1Password, for example, keep `op://` references in a `.op.env` file (it is
gitignored, like `.env`) and run:

```bash
op run --env-file=.op.env -- make doctor
op run --no-masking --env-file=.op.env -- make provision
```

Use `--no-masking` for `make provision`: masking pipes the output, and the interactive prompts
can't draw correctly through a pipe. The script never prints secrets.

## Files

- `setup-vps.py` — creates and sets up a server (`make provision`)
- `doctor.py` — checks your setup (`make doctor`)
- `cloud-config.yaml.tmpl` — what gets installed and configured on the server
- `.env.example` — template for your `.env`
- `Brewfile` — macOS tools for `brew bundle`
- `ROADMAP.md` — ideas considered and deferred
