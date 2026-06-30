# Roadmap

Ideas considered and deliberately deferred. Not commitments — a parking lot so we
don't re-derive them. Context: comparison against
[pocketdev](https://github.com/0xMassi/pocketdev) (same idea — Hetzner + Tailscale +
Claude Code), June 2026.

## Tabled

- **`destroy` / teardown command.** Delete a Hetzner server by name + remove its
  Tailscale node in one shot. Deferred: I run at most ~2 boxes and manual shutdown
  or deletion via the Hetzner console is fine. Revisit if box count grows or I start
  spinning disposable boxes up and down.

- **Persistent web preview (`publish`).** The first cut (see below, being built) is
  an *ephemeral* `cloudflared` quick-tunnel — random URL, no account. A persistent
  named tunnel (stable hostname) needs a Cloudflare Zero Trust account + token.
  Tabled until I actually want a stable URL.

- **Project bootstrap (clone repo / rsync local folder).** pocketdev offers this at
  provision time. Doing it manually after SSH is fine for now.

- **Single-use / ephemeral Tailscale auth keys.** Fits pocketdev's disposable-box
  model, not mine (persistent boxes — an ephemeral node deregisters after extended
  downtime). Tagging the node (ACL scoping + disabling the 180-day key-expiry
  re-auth) is the part worth doing; see the live discussion.

## Considered & dropped

- **Privilege separation (no-sudo `claude` user for the agent).** pocketdev runs
  agents as a sudo-less `dev` user. Dropped here because the agent needs Docker, and
  the `docker` group is root-equivalent — so a no-sudo user would be *cosmetic*, not a
  real boundary, while adding `sudo -iu claude` friction to every session (annoying
  from a phone/Termius). Accepted trade-off: Claude runs as `sysadmin` (full sudo +
  docker). If the agent's blast radius ever needs to be real, revisit with a user
  that has neither sudo *nor* docker.

## Shipped (out of the comparison)

- **Mosh** + app-agnostic mobile connect (QR encoding `ssh://…`), no public ports.
- **Ephemeral `publish <port>`** via `cloudflared` quick-tunnel.
- **Tagged Tailscale node** (`tag:vps`) — ACL scoping + no 180-day re-auth.
- **UX polish**: per-type cost, summary panel, stepped/elapsed progress, optional
  `~/.ssh/config` append.
