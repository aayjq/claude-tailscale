# === Claude cloud session -> your Tailscale devices (generic, no account-specific values) ===
# Per-user config: env var TAILSCALE_AUTHKEY (optional; reusable+ephemeral key of YOUR tailnet).
if ! command -v tailscaled >/dev/null; then
  V=$(curl -fsSL https://pkgs.tailscale.com/stable/ | grep -o 'tailscale_[0-9.]*_amd64.tgz' | head -1)
  curl -fsSL "https://pkgs.tailscale.com/stable/$V" | tar xz -C /tmp && cp /tmp/tailscale_*_amd64/tailscale* /usr/local/bin/ || true
fi
command -v ssh >/dev/null || apt-get install -y -qq openssh-client || true
cat > /usr/local/bin/ts-up <<'EOF'
#!/bin/bash
# Join this container to your tailnet (userspace mode) and write SSH aliases. Safe to re-run.
L=/var/lib/tailscale; mkdir -p $L /var/run/tailscale /root/.ssh/config.d
K="$TAILSCALE_AUTHKEY"; H="${TS_HOSTNAME:-claude-cloud}"
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
if ! online && [ -n "$K" ]; then
  tailscale logout >/dev/null 2>&1; pkill -x tailscaled; rm -f $L/tailscaled.state; start
  tailscale up --hostname="$H" --auth-key="$K" --timeout=30s
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
EOF
