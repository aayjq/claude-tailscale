# === Claude cloud session -> your Tailscale devices (generic, no account-specific values) ===
# Login, first match wins:
#   1. Tailscale API token saved as a network secret for api.tailscale.com (recommended):
#      ts-up mints a one-time, 10-minute, ephemeral key tagged $TS_TAG (default tag:claude).
#   2. env var TAILSCALE_AUTHKEY (reusable + ephemeral auth key of YOUR tailnet).
#   3. a login URL for the user to approve.
if ! command -v tailscaled >/dev/null; then
  V=$(curl -fsSL https://pkgs.tailscale.com/stable/ | grep -o 'tailscale_[0-9.]*_amd64.tgz' | head -1)
  curl -fsSL "https://pkgs.tailscale.com/stable/$V" | tar xz -C /tmp && cp /tmp/tailscale_*_amd64/tailscale* /usr/local/bin/ || true
fi
command -v ssh >/dev/null || { apt-get update -qq && apt-get install -y -qq openssh-client; } >/dev/null 2>&1 || true
cat > /usr/local/bin/ts-up <<'EOF'
#!/bin/bash
# Join this container to your tailnet (userspace mode) and write SSH aliases. Safe to re-run.
L=/var/lib/tailscale; mkdir -p $L /var/run/tailscale /root/.ssh/config.d
H="${TS_HOSTNAME:-claude-cloud}"; T="${TS_TAG:-tag:claude}"
mint() {  # one-time tagged key from the Tailscale API; the proxy adds the saved token
  curl -fsS -X POST https://api.tailscale.com/api/v2/tailnet/-/keys -H 'Content-Type: application/json' \
    -d "{\"capabilities\":{\"devices\":{\"create\":{\"reusable\":false,\"ephemeral\":true,\"preauthorized\":true,\"tags\":[\"$T\"]}}},\"expirySeconds\":600,\"description\":\"ts-up\"}" \
    2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])' 2>/dev/null
}
start() {
  pkill -x tailscaled; sleep 1; echo "$HTTPS_PROXY" > $L/proxy
  setsid nohup tailscaled --tun=userspace-networking --state=$L/tailscaled.state >>$L/tailscaled.log 2>&1 </dev/null &
  sleep 3
}
online() {  # self is Running and not marked offline (ephemeral nodes get deleted while idle)
  for i in 1 2 3 4 5; do S=$(tailscale status --peers=false 2>/dev/null) && ! grep -q offline <<<"$S" && return 0; sleep 2; done
  return 1
}
if ! pgrep -x tailscaled >/dev/null || [ "$(cat $L/proxy 2>/dev/null)" != "$HTTPS_PROXY" ]; then start; fi
if ! online; then
  K=$(mint); [ -n "$K" ] && echo "Using a one-time $T key from the Tailscale API" || K="$TAILSCALE_AUTHKEY"
  if [ -n "$K" ]; then
    tailscale logout >/dev/null 2>&1; pkill -x tailscaled; rm -f $L/tailscaled.state; start
    tailscale up --hostname="$H" --auth-key="$K" --timeout=30s
  fi
fi
if ! online; then
  tailscale up --hostname="$H" --force-reauth --timeout=15s
  echo "NEEDS LOGIN: send the URL above to the user; after they approve, run ts-up again."; exit 1
fi
sleep 2
tailscale status --json | python3 -c '
import json, sys
d = json.load(sys.stdin)
for p in (d.get("Peer") or {}).values():
    n, ip = p["DNSName"].split(".")[0], p["TailscaleIPs"][0]
    if n: print(f"Host {n}\n  HostName {ip}\n  ProxyCommand tailscale nc %h %p\n")
' > /root/.ssh/config.d/tailscale
tailscale status
EOF
chmod +x /usr/local/bin/ts-up
mkdir -p /root/.ssh/config.d && chmod 700 /root/.ssh
cat > /root/.ssh/config <<'EOF'
Include config.d/*
Host 100.* *.ts.net
  ProxyCommand tailscale nc %h %p
Host *
  StrictHostKeyChecking accept-new
  ConnectTimeout 30
EOF
chmod 600 /root/.ssh/config
mkdir -p /root/.claude
cat > /root/.claude/CLAUDE.md <<'EOF'
# Remote devices via Tailscale
- The user set up this environment so Claude can reach their own Tailscale devices.
  Run `ts-up` when a task needs one (joins the tailnet in userspace mode via DERP relays).
- Then `ssh <device-name> '<cmd>'` (aliases are generated from `tailscale status`); scp/rsync work too.
  `ssh <user>@100.x.y.z` also works. Plain outbound TCP does not work here; always go through Tailscale.
- If `ts-up` prints a login URL, give it to the user, wait for approval, then run `ts-up` again.
- If ssh fails after the session was idle or resumed, run `ts-up` again first.
## Managing the tailnet (only if a Tailscale API token is saved for api.tailscale.com)
- The user's model: on a new device they only install Tailscale and run `tailscale up --ssh`; Claude does the rest.
- New device: find it with `curl -s https://api.tailscale.com/api/v2/tailnet/-/devices`, then
  `POST /api/v2/device/<id>/tags {"tags":["tag:server"]}` and `POST /api/v2/device/<id>/key {"keyExpiryDisabled":true}`,
  then run `ts-up` again to refresh SSH aliases. Sessions (tag:claude) can only reach tag:server devices.
- ACL changes: GET /api/v2/tailnet/-/acl (note the ETag), POST .../acl/validate, show the user the change and get
  their OK, then POST .../acl with If-Match. Never weaken access for the user's own devices.
## Delegating hands-on work to Qoder on a server (`qoder-task`)
- If a server has Qoder CLI (`qoderclicn` or `qodercli`): you plan and verify, Qoder does the hands-on work there.
  Do quick checks yourself with plain ssh; give coding/deploy/debug subtasks to Qoder with clear acceptance tests:
  `ssh <dev> 'qoder-task -d /srv/<app> -n <name> "<task>"'` (scoped to that dir; `-a <path>` widens it).
- May take over ~5 min: add `-b`, then `qoder-task wait <id>`. `-c` continues Qoder's last session in that dir.
- After each task verify it yourself: service status, curl, and `qoder-task changes <id>` (edits outside the dir).
- Never delegate risky operations (deleting data, firewall, SSH/users, reboot, unrelated services):
  ask the user first, then do them yourself with plain ssh.
- If `qoder-task` is missing on the server, install it there:
  `curl -fsSL https://raw.githubusercontent.com/aayjq/claude-tailscale/f31f02a7cbb020e9b9c5efc2e9dd2009edba3a10/extras/qoder-task -o /usr/local/bin/qoder-task && chmod 755 /usr/local/bin/qoder-task`
EOF
