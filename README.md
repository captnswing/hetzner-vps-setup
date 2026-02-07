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

### 6. SSH Config for Ghostty Terminal (Required for Ghostty users)

If you use [Ghostty terminal](https://ghostty.org/), you need to configure SSH for full terminal support.

#### Create SSH Config Alias

Add an entry for each server in `~/.ssh/config`:

```
Host myserver
	HostName 100.x.x.x
	User sysadmin
	IdentityFile ~/.ssh/Hetzner_Automation_Key
	SetEnv TERM=xterm-ghostty
```

**Why `SetEnv TERM=xterm-ghostty`?**  
Tailscale SSH doesn't automatically forward the TERM environment variable. Without this, terminal applications like
`htop`, `vim`, and `tmux` will fail with "Error opening terminal: unknown".

#### Install Ghostty Terminfo on First Connect

On your **first connection** to a new server, run this one-time setup command:

```bash
# Replace 'myserver' with your SSH config alias
infocmp -x | ssh myserver "cat > /tmp/terminfo.txt && tic -x - && tic -x -o /usr/share/terminfo /tmp/terminfo.txt && rm -f /tmp/terminfo.txt"
```

This installs the Ghostty terminfo database both for your user (`~/.terminfo/`) and system-wide (
`/usr/share/terminfo/`). The system-wide installation is required for commands run with `sudo` (like `sudo iotop`).

**One-time only**: The cloud-config provisioning makes `/usr/share/terminfo/` writable by the `admin` group, so you
don't need sudo. This command only needs to run once per server.

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

#### Recommended: Create SSH Config Alias

For easier access and proper terminal support (especially with Ghostty), create an SSH config alias:

```bash
# Add to ~/.ssh/config
Host clawdbot
	IdentityFile ~/.ssh/Hetzner_Automation_Key
	HostName 100.x.x.x
	User sysadmin
	SetEnv TERM=xterm-ghostty
```

**Why `SetEnv TERM=xterm-ghostty`?**  
Tailscale SSH doesn't automatically forward the TERM environment variable. Without this, terminal applications like
`htop`, `vim`, and `tmux` won't work correctly (you'll see "Error opening terminal: unknown").

Then connect simply with:

```bash
ssh clawdbot
```

The `LocalCommand` in your global SSH config (if configured) will also automatically sync the Ghostty terminfo database
on first connection.

## Using the Shell

### History Search with fzf

All provisioned servers include `fzf` for powerful command history search:

**Press Ctrl+R** to search command history:

- Type any part of a command you've run before
- Use **arrow keys** to navigate matches
- Press **Enter** to execute, **Esc** to cancel
- Press **?** to toggle preview window

**Search examples:**

```bash
# Press Ctrl+R and type:
docker ps      # Find all docker ps commands
git commit     # Find all git commits
ssh help       # Find ssh commands to servers

# Fuzzy matching (non-consecutive characters):
dps           # Finds "docker ps"
gcm           # Finds "git commit"
```

**Advanced search operators:**

- `'docker` - Commands starting with "docker" (use `'` prefix)
- `!docker` - Commands NOT containing "docker" (use `!` to exclude)
- `docker | git` - Commands with "docker" OR "git" (use `|` for OR)

**Other fzf shortcuts:**

- **Ctrl+T** - Search files/directories and insert at cursor
- **Esc, then C** - Search and `cd` into a directory

### History Features

Your command history includes:

- **50,000 commands** stored in memory
- **100,000 lines** saved to `~/.bash_history`
- **Timestamps** showing when each command was run
- **No duplicates** - automatically removed
- **Immediate saving** - history preserved even on crashes
- **Filtered trivial commands** - `ls`, `cd`, `pwd` not cluttering history

View history with timestamps:

```bash
history | tail -20
```

## What Gets Installed

The provisioning happens in two phases:

### Phase 1: Cloud-Init (`cloud-config.yaml.tmpl`)

Cloud-init runs on first boot and configures the base system:

#### User Account

- Username: `sysadmin`
- Groups: `users`, `admin`, `docker`
- Passwordless sudo enabled
- SSH key authentication only (no password)
- Home directory populated with skeleton files (`.bashrc`, `.profile`, etc.)

#### Packages Installed

- **System utilities**: `ufw`, `unattended-upgrades`, `git`, `curl`, `wget`, `jq`, `vim`, `tmux`, `ripgrep`, `fzf`
- **Monitoring tools**: `ncdu` (disk usage), `htop`, `iotop`
- **Docker**: `docker.io`, `docker-compose-v2`
- **GitHub CLI**: `gh`

#### System Configuration

- **Timezone**: Set to `Europe/Berlin`
- **NTP**: Time synchronization enabled
- **Swap**: 2GB swap file created at `/swapfile`
- **Locale**: Configured for UTF-8

#### Shell Enhancements

**Bash History** (configured in `.bashrc`):

- History size increased to **50,000 commands** in memory (was 1,000)
- History file size increased to **100,000 lines** (was 2,000)
- **Timestamps** added to each command (`HISTTIMEFORMAT`)
- **Immediate history saving** - commands saved after each execution, not just on shell exit
- **Duplicate removal** - `erasedups` removes duplicate entries
- **Ignored commands** - trivial commands (`ls`, `cd`, `pwd`, `exit`, `clear`, `history`) not saved

**Interactive History Search**:

- `fzf` installed and configured for fuzzy history search
- Features: fuzzy matching, preview, scrolling through history with arrow keys

| Shortcut | Function                              |
|----------|---------------------------------------|
| Ctrl+R   | Search command history (fuzzy search) |
| Ctrl+T   | Search files (insert path at cursor)  |
| Esc, C   | Search directories (cd into selected) | 

#### Files Written (`write_files` section)

1. **Docker daemon config** (`/etc/docker/daemon.json`):
    - Log rotation: max 10MB per file, 3 files kept
    - Live-restore enabled
2. **Docker cleanup cron** (`/etc/cron.weekly/docker-cleanup`):
    - Weekly cleanup of images/containers older than 7 days
3. **Custom MOTD** (`/etc/update-motd.d/99-custom`):
    - Displays system stats on login: hostname, uptime, memory, disk, Docker containers

#### Runtime Commands (`runcmd` section)

Executed in order after packages are installed:

1. **User Home Setup**:
    - Copy skeleton files from `/etc/skel/` to `/home/sysadmin/`
    - Fix ownership and permissions

2. **Ghostty Terminal Support**:
    - Modify `.bashrc` to recognize `xterm-ghostty` terminal type
    - Enable colored prompt and truecolor (24-bit color) support

3. **Disable Sudo Hint**:
    - Create `.sudo_as_admin_successful` to suppress "To run a command as administrator..." message
    - Keeps MOTD visible

4. **Firewall (UFW)**:
    - Allow port 443/tcp (HTTPS)
    - Allow port 41641/udp (Tailscale)
    - Allow all traffic on `tailscale0` interface
    - Enable firewall

5. **Docker**:
    - Restart Docker daemon with new config
    - Add `sysadmin` user to `docker` group

6. **Automatic Updates**:
    - Enable and start `unattended-upgrades` for security patches

7. **Tailscale VPN**:
    - Install Tailscale from official repository
    - Connect to your tailnet with auth key
    - Enable Tailscale SSH (`--ssh`)
    - Accept subnet routes (`--accept-routes`)

8. **Reboot**:
    - System reboots to apply all changes

### Phase 2: Client-Side SSH Config (Optional)

If you use Ghostty terminal, the SSH `LocalCommand` automatically installs the terminfo database on first connection,
enabling full terminal features (colors, special keys, etc.)

### Security Features

- **UFW firewall**: Only port 443/tcp and Tailscale (41641/udp) open from internet
    - SSH only accessible via Tailscale network (more secure than public SSH)
    - All traffic allowed on `tailscale0` interface
- **Automatic security updates**: Via `unattended-upgrades`
- **Tailscale SSH**: Replaces traditional SSH, uses your tailnet authentication (no SSH keys needed when connecting via
  Tailscale)

## How It Works

### Setup Script (`setup-vps.py`)

The Python script automates the entire provisioning workflow:

1. **Load Configuration**:
    - Reads environment variables from `.env`
    - Validates required credentials (Hetzner API token, SSH key, Tailscale auth key)

2. **Interactive Prompts**:
    - **Hostname**: Checks availability against existing Hetzner servers in your account
    - **Server Type**: Displays available VPS types with specs (vCPU, RAM, storage)
    - **Datacenter**: Lists available regions (Helsinki, Frankfurt, etc.)

3. **Cloud-Config Templating**:
    - Reads `cloud-config.yaml.tmpl`
    - Substitutes variables: `${hostname}`, `${pub_key}`, `${tailscale_key}`

4. **Server Creation**:
    - Creates server via Hetzner Cloud API
    - Attaches your SSH key for initial access
    - Passes cloud-config as user-data for cloud-init

5. **Post-Creation Monitoring**:
    - Polls local Tailscale daemon to find the server's VPN IP (timeout: 5 minutes)
    - Waits for SSH to become available (timeout: 5 minutes)
    - Displays connection command with Tailscale IP

6. **Output**:
    - Shows public IPv4 address (for fallback access)
    - Shows Tailscale VPN IP (preferred, more secure)
    - Provides ready-to-use SSH command

### Key Features

- **Parallel execution**: Uses Rich library for progress spinners and status updates
- **Error handling**: Validates hostname uniqueness, server type availability, API responses
- **Graceful cancellation**: Ctrl+C at any prompt safely exits
- **Tailscale integration**: Automatically resolves VPN IPs using local Tailscale CLI

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

### Colors don't work in terminal (htop, etc.)

If you see `Error opening terminal: xterm-ghostty`, the terminfo database hasn't been installed yet.

**Solution**: Run the one-time terminfo installation command (see Section 6 above):

```bash
# Use your SSH config alias
infocmp -x | ssh myserver "cat > /tmp/terminfo.txt && tic -x - && tic -x -o /usr/share/terminfo /tmp/terminfo.txt && rm -f /tmp/terminfo.txt"
```

This installs terminfo both for your user and system-wide (for sudo commands).

### sudo commands fail with terminal errors (iotop, etc.)

If `sudo iotop` shows `setupterm: could not find terminal`, the terminfo is installed for your user but not system-wide.

**Solution**: The installation command in Section 6 installs both. If you previously only installed user-level terminfo,
re-run the full command:

```bash
infocmp -x | ssh myserver "cat > /tmp/terminfo.txt && tic -x - && tic -x -o /usr/share/terminfo /tmp/terminfo.txt && rm -f /tmp/terminfo.txt"
```

## Tailscale SSH & Access Control

### How Tailscale SSH Works

When you provision a server with this setup, **Tailscale SSH is enabled** (`--ssh` flag). This means:

- ✅ SSH access is controlled by your **Tailscale ACL policy**, not by SSH keys in `~/.ssh/authorized_keys`
- ✅ Authentication uses your Tailscale identity (MagicDNS + your login)
- ✅ More secure: SSH is only accessible via the Tailscale VPN network (not exposed to internet)
- ✅ No need to manage SSH keys manually

### Restricting Access to Specific Users

By default, anyone in your Tailscale network (tailnet) can access all machines. To restrict SSH access to only specific
users:

1. Go to [Tailscale Admin Console - ACLs](https://login.tailscale.com/admin/acls)
2. Edit the policy file (JSON format)
3. Add SSH-specific rules:

```json
{
  "acls": [
    {
      "action": "accept",
      "src": [
        "your-email@domain.com"
      ],
      "dst": [
        "*:*"
      ]
    }
  ],
  "ssh": [
    {
      "action": "accept",
      "src": [
        "your-email@domain.com"
      ],
      "dst": [
        "tag:vps"
      ],
      "users": [
        "sysadmin",
        "root"
      ]
    }
  ]
}
```

**Important**:

- ACLs are managed through the web console or Tailscale API (not via CLI)
- Changes apply immediately without restarting services
- The `~/.ssh/authorized_keys` file is bypassed entirely when using Tailscale SSH
- See [Tailscale ACL documentation](https://tailscale.com/kb/1018/acls/) for advanced policies

## Files

- `setup-vps.py` - Interactive server provisioning
- `cloud-config.yaml.tmpl` - Cloud-init template for server hardening
- `.env.example` - Example environment variables (copy to `.env`)
