# Resource agent: connects OUT to the relay, waits for jobs, runs only whitelisted actions (via devctl).
# No arbitrary commands. Never sends this machine's hostname or addresses; IPs in text output are masked.
#   python3 agent.py RELAY_HOST ALIAS TOKEN_FILE RELAY_CERT
import json, subprocess, base64, re, os, sys, time

HOST, ALIAS, TOKEN_FILE, CERT = sys.argv[1:5]
TOKEN = open(TOKEN_FILE).read().strip()
DEVCTL = os.environ.get("DEVCTL", "/usr/local/bin/devctl")
SAFE_ARG = re.compile(r"[A-Za-z0-9_./-]{0,200}")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
HEXCOLON = re.compile(r"(?<![0-9A-Za-z:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,}(?![0-9A-Za-z:])")
MAX_FILE = 8_000_000

def proxy_for(host):
    """HTTPS proxy to use for `host`, honouring NO_PROXY (exact names/addresses and .suffix entries)."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    for e in (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "").split(","):
        e = e.strip()
        if e and (e == host or (e.startswith(".") and host.endswith(e))):
            return None
    return proxy.split("://", 1)[-1] if proxy else None

def redact(text):
    # IPv4 always; colon-separated hex only when it is an IPv6 ("::" or 4+ colons) or a MAC address,
    # so clock times like 12:30:45 and USB ids like 0627:0001 stay readable.
    text = IPV4.sub("[ip-hidden]", text)
    return HEXCOLON.sub(lambda m: "[ip-hidden]" if "::" in m.group(0) or m.group(0).count(":") >= 4 else m.group(0), text)

def run(*cmd):
    r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=90)
    return r.returncode == 0, redact((r.stdout + r.stderr).strip())

def send_file(path, name):
    with open(path, "rb") as f:
        data = f.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        return False, "file too large (limit 8 MB)", []
    return True, "%s (%d bytes)" % (name, len(data)), [{"name": name, "b64": base64.b64encode(data).decode()}]

# ---- whitelisted actions ----
def a_list():                return run(DEVCTL, "list")
def a_events():              return run(DEVCTL, "events", "20")
def a_disk_ls(dev, sub=""):  return run(DEVCTL, "disk", "ls", dev, *([sub] if sub else []))
def a_disk_get(dev, rel):
    ok, mp = run(DEVCTL, "disk", "mount", dev)
    if not ok:
        return False, mp, []
    root = os.path.realpath(mp)
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return False, "no such file on that disk", []
    return send_file(full, os.path.basename(full))
def a_cam_snap(src="fake"):
    ok, out = run(DEVCTL, "cam", "snap", src)
    if not ok:
        return False, out, []
    path = out.splitlines()[-1]
    try:
        return send_file(path, "snapshot.jpg")
    finally:
        os.remove(path)
def a_fake_disk(op, name):   # test helper: plug/unplug a simulated USB stick
    return run(DEVCTL, "fake", {"add": "disk-add", "remove": "disk-remove"}.get(op, "?"), name)

ACTIONS = {"list": a_list, "events": a_events, "disk_ls": a_disk_ls, "disk_get": a_disk_get,
           "cam_snap": a_cam_snap, "fake_disk": a_fake_disk}

def do_job(job):
    fn, args = ACTIONS.get(job.get("action")), job.get("args") or []
    if not fn:
        return False, "action not allowed: %s (allowed: %s)" % (job.get("action"), ", ".join(sorted(ACTIONS))), []
    if not all(SAFE_ARG.fullmatch(a) for a in args) or any(".." in a.split("/") for a in args):
        return False, "argument rejected", []
    try:
        res = fn(*args)
    except TypeError:
        return False, "wrong number of arguments", []
    except Exception as e:
        return False, "error: " + type(e).__name__, []
    return (list(res) + [[]])[:3]

PROXY = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").split("://", 1)[-1]

def post(path, obj, timeout):
    # Talk to the relay through `openssl s_client`: it pins the relay's own certificate, sends the SNI that
    # an egress proxy may demand even for an IP address (Python's ssl module omits SNI for IPs), and goes
    # through HTTPS_PROXY when this machine can only reach the internet that way.
    body = json.dumps(obj).encode()
    req = ("POST %s HTTP/1.0\r\nHost: %s\r\nAuthorization: Bearer %s\r\nContent-Type: application/json\r\n"
           "Content-Length: %d\r\n\r\n" % (path, HOST, TOKEN, len(body))).encode() + body
    cmd = ["openssl", "s_client", "-quiet", "-verify_quiet", "-verify_return_error", "-CAfile", CERT,
           "-servername", HOST, "-connect", HOST + ":443"]
    proxy = proxy_for(HOST)
    if proxy:
        cmd += ["-proxy", proxy]
    p = subprocess.run(cmd, input=req, capture_output=True, timeout=timeout)
    head, _, rest = p.stdout.partition(b"\r\n\r\n")
    if not head.startswith(b"HTTP/"):
        raise ConnectionError("no valid reply")
    return json.loads(rest or b"{}")

print("agent", ALIAS, "started", flush=True)
while True:
    try:
        job = post("/agent/poll", {"alias": ALIAS, "actions": sorted(ACTIONS)}, 40).get("job")
        if job:
            ok, text, files = do_job(job)
            post("/agent/result", {"id": job["id"], "ok": ok, "output": text, "files": files}, 60)
            print(time.strftime("%F %T"), "did", job.get("action"), "ok" if ok else "failed", flush=True)
    except Exception as e:
        print("relay unreachable (%s), retrying" % type(e).__name__, flush=True)
        time.sleep(5)
