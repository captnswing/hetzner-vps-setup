# Agent Guidelines for hetzner-vps-setup

This repository automates provisioning of hardened Ubuntu VPS servers on Hetzner Cloud with Tailscale VPN, Docker, and developer tools.

## Project Overview

**Type**: Python CLI automation script  
**Purpose**: Interactive VPS provisioning with cloud-init configuration  
**Runtime**: Python 3.14+ with `uv` package manager  
**Primary File**: `setup-vps.py` (341 lines)

## Build & Run Commands

### Install Dependencies
```bash
make install
```

### Run Script
```bash
# Source environment first (required)
source .env

# Run the script
uv run setup-vps.py
```

**Note**: No test suite exists. Manual testing via actual VPS provisioning.

### Development Setup
```bash
# Install dependencies
make install

# Copy environment template
cp .env.example .env

# Edit .env with your credentials (see .env.example for structure)
# Required: HCLOUD_TOKEN, SSH_KEY_NAME, PUB_KEY
# Optional: TAILSCALE_AUTH_KEY, GITHUB_TOKEN

# Run with uv (handles dependencies automatically)
uv run setup-vps.py
```

### Linting & Formatting
```bash
make lint  # Runs ruff format and ruff check --fix
```

## Code Style Guidelines

### Imports
- **Standard library first**, then third-party, blank line separated
- Alphabetically sorted within each group
- Use `from X import Y` for specific imports
- Example from `setup-vps.py`:
  ```python
  import os
  import subprocess
  import sys
  import time
  from pathlib import Path
  from string import Template

  import questionary
  from dotenv import load_dotenv
  from hcloud import Client
  from rich.console import Console
  ```

### Type Hints
- **Use type hints** for function signatures (return types mandatory)
- Use modern syntax: `str | None` (not `Optional[str]`)
- Example:
  ```python
  def get_tailscale_ip(hostname: str, timeout: int = 300) -> str | None:
      """Docstring here."""
      ...
  ```

### Naming Conventions
- **Variables/functions**: `snake_case` (e.g., `public_ip`, `wait_for_ssh`)
- **Constants**: `SCREAMING_SNAKE_CASE` (e.g., `HCLOUD_TOKEN`, `SSH_KEY_PATH`)
- **Classes**: `PascalCase` (none in this project, but follow if adding)
- **Private/internal**: prefix with `_` (not used currently)

### Docstrings
- Use triple-quoted strings for function/class documentation
- Focus on *what* and *why*, not *how* (code shows how)
- Example:
  ```python
  def prompt_hostname(client: Client) -> str:
      """Prompt user for hostname and check availability."""
      ...
  ```

### String Formatting
- Prefer **f-strings** for interpolation: `f"Server {hostname} created"`
- Use `Template` for multi-line configs with many substitutions (see `cloud-config.yaml.tmpl`)
- Avoid `%` formatting and `.format()`

### Error Handling
- **Catch specific exceptions** when possible: `APIException`, `FileNotFoundError`
- Use broad `Exception` only for availability checks (non-critical)
- **Exit with status 1** on fatal errors: `sys.exit(1)`
- Display errors via Rich console: `console.print(f"[bold red]Error:[/bold red] {message}")`
- Example:
  ```python
  try:
      response = client.servers.create(...)
  except APIException as e:
      console.print(f"[bold red]Failed to create server:[/bold red] {e.code} - {e.message}")
      sys.exit(1)
  ```

### User Interaction
- **Use `questionary`** for interactive prompts (text input, select, confirm)
- **Use Rich** for output formatting:
  - `console.print()` for styled output
  - `Progress` with spinners for API calls
  - `Table` for structured data display
  - `Panel` for section headers
- Validate user input (e.g., hostname availability check)
- Allow `Ctrl+C` to exit gracefully: check for `None` return from questionary

### File Operations
- Use **`pathlib.Path`** (not `os.path`)
- Expand user paths: `Path("~/.ssh/key").expanduser()`
- Read files: `Path("file.txt").read_text()`

### Environment Variables
- Load with `python-dotenv`: `load_dotenv(".env")`
- Access via `os.getenv()` with defaults where appropriate
- **Always use empty string default** for optional vars: `os.getenv("VAR", "")` (never allow `None` - it renders as `"None"` string in templates)
- Validate **required** variables at startup (lines 32-35)

### Subprocess Calls
- Use `subprocess.run()` with `capture_output=True, text=True`
- Set timeouts to prevent hanging: `timeout=10`
- Check `returncode` for success/failure
- Handle `FileNotFoundError` for missing binaries (e.g., `tailscale`)

### Progress Indication
- **Always use Rich Progress** for long-running operations (API calls, SSH waits)
- Spinner + text description pattern:
  ```python
  with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
      task = progress.add_task("Loading...", total=None)
      # do work
      progress.stop()  # if early exit needed
  ```

### Configuration Templates
- Store cloud-init in `cloud-config.yaml.tmpl` with `${variable}` placeholders
- Use `string.Template` for substitution (safe, prevents code injection)
- Keep templates in root directory alongside script

## Architecture & Patterns

### Script Structure
1. **Imports & globals** (lines 1-36)
2. **Helper functions** (lines 38-254)
   - `get_tailscale_ip()` - Poll for VPN IP
   - `wait_for_ssh()` - Check SSH availability
   - `prompt_*()` - Interactive user input
   - `check_server_type_availability()` - Validation
3. **Main function** (lines 256-366)
   - Linear workflow: validate → prompt → create → wait → output

### Key External Dependencies
- **hcloud**: Hetzner Cloud API client
- **questionary**: Interactive prompts
- **rich**: Terminal formatting and progress
- **dotenv**: Environment variable loading

### Security Considerations
- **Never commit `.env`** - contains secrets
- Use `.env.example` for structure documentation
- SSH keys loaded from environment, not hardcoded
- Firewall (UFW) configured via cloud-init
- Tailscale provides VPN-only access (no public SSH)

### Cloud-Init Workflow
- Script generates `user_data` from template
- Hetzner provisions server with Ubuntu 24.04
- cloud-init runs setup commands on first boot
- Server reboots after configuration complete
- Script polls for Tailscale IP, then SSH availability

## Common Patterns

### API Calls with Progress
```python
with Progress(...) as progress:
    task = progress.add_task("Loading...", total=None)
    data = client.api_call()
```

### Interactive Prompts with Validation
```python
while True:
    value = questionary.text("Enter value:").ask()
    if value is None:  # Ctrl+C
        sys.exit(0)
    if validate(value):
        break
    console.print("[bold red]Invalid, try again[/bold red]")
```

### Subprocess with Timeout
```python
result = subprocess.run(
    ["command", "arg"],
    capture_output=True,
    text=True,
    timeout=10,
)
if result.returncode == 0:
    # success
```

### Rich Console Colors
- `[bold green]✓[/bold green]` - Success
- `[bold red]✗[/bold red]` - Error
- `[yellow]Warning:[/yellow]` - Warning
- `[cyan]Info[/cyan]` - Informational

## Files You Should Know

- **`setup-vps.py`** - Main provisioning script (341 lines)
- **`cloud-config.yaml.tmpl`** - Cloud-init template for server setup (168 lines)
- **`.env.example`** - Environment variable structure (no secrets)
- **`README.md`** - User documentation with prerequisites and usage
- **`Makefile`** - Build automation (install, lint, format targets)

## Constraints & Gotchas

1. **Requires active Tailscale daemon** on local machine for IP resolution
2. **Hetzner API token** must have Read & Write permissions
3. **SSH key name** in Hetzner must match `SSH_KEY_NAME` exactly
4. **Hostname uniqueness** checked before creation (per Hetzner account)
5. **No rollback** - failed provisions leave orphaned resources (manual cleanup)
6. **Cloud-init takes ~3-5 minutes** - script polls for readiness
7. **uv handles dependencies** - no pip install needed

## Making Changes

### Adding New Prompts
1. Create `prompt_*()` function following existing pattern
2. Add questionary interaction with validation loop
3. Return value or `sys.exit(0)` on cancel
4. Call from `main()` before server creation

### Modifying Cloud-Init
1. Edit `cloud-config.yaml.tmpl` (YAML syntax)
2. Add placeholders: `${variable_name}`
3. Update `config.substitute()` call in `main()` (line 316-321)
4. Test with `uv run setup-vps.py` (provisions real server)

### Adding Environment Variables
1. Add to `.env.example` with comments
2. Add `os.getenv("VAR_NAME")` in globals section
3. Add validation check if required (lines 32-35)
4. Use in cloud-init template or script logic

## Common Tasks

**Test script locally** (no provisioning):
```bash
# Comment out server creation in main() first
source .env && uv run setup-vps.py
```

**Update dependencies**:
```bash
# uv handles this automatically, no lock file management needed
# Just update import statements in setup-vps.py
```

**Debug cloud-init**:
```bash
# SSH into server after provisioning
ssh sysadmin@<tailscale-ip>

# Check logs
sudo cat /var/log/cloud-init-output.log
```

## Notes for AI Agents

- This is a **single-file script** - keep it that way unless complexity demands modules
- **No tests** exist - changes require manual verification via actual provisioning
- **Error paths are critical** - API failures should never leave silent state
- **User experience matters** - use Rich formatting consistently, provide clear feedback
- **Cloud-init is declarative** - changes require server rebuild to test
- **Dependencies are pinned by uv** - check `uv.lock` if present (not currently in repo)
