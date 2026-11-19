# Relay ("post office"): resource agents poll it for jobs; the controller (Claude) only talks to it.
# Neither side learns the other's address. It never logs peer IP addresses.
#   python3 relay.py DIR      (DIR holds tls.crt, tls.key, client.token, agent.token)
import http.server, ssl, json, threading, uuid, time, re, sys, os

DIR = sys.argv[1]
CLIENT_TOKEN = open(os.path.join(DIR, "client.token")).read().strip()
AGENT_TOKEN = open(os.path.join(DIR, "agent.token")).read().strip()
ALIAS = re.compile(r"[A-Za-z0-9_-]{1,32}")
lock = threading.Lock()
agents = {}    # alias -> {"jobs": [...], "cv": Condition, "last": ts, "actions": [...]}
pending = {}   # job id -> {"cv": Condition, "result": dict|None}

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def setup(self):
        self.request.settimeout(60)
        self.request.do_handshake()   # TLS handshake here, in this connection's own thread
        super().setup()
    def log_message(self, *a):  # default logging would record client IPs: disabled on purpose
        pass
    def reply(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n <= 20_000_000 else {}
    def authed(self, token):
        return self.headers.get("Authorization", "") == "Bearer " + token

    def do_GET(self):
        if self.path != "/devices":
            return self.reply(404, {"error": "not found"})
        if not self.authed(CLIENT_TOKEN):
            return self.reply(401, {"error": "bad token"})
        now = time.time()
        with lock:
            devs = [{"alias": a, "online": now - s["last"] < 40, "actions": s["actions"]} for a, s in sorted(agents.items())]
        self.reply(200, {"devices": devs})

    def do_POST(self):
        if self.path == "/agent/poll":
            if not self.authed(AGENT_TOKEN):
                return self.reply(401, {"error": "bad token"})
            b = self.body()
            alias = str(b.get("alias", ""))
            if not ALIAS.fullmatch(alias):
                return self.reply(400, {"error": "bad alias"})
            with lock:
                s = agents.setdefault(alias, {"jobs": [], "cv": threading.Condition(lock), "last": 0, "actions": []})
                s["last"] = time.time()
                s["actions"] = [str(x) for x in b.get("actions", [])][:50]
                if not s["jobs"]:
                    s["cv"].wait(timeout=25)
                s["last"] = time.time()
                job = s["jobs"].pop(0) if s["jobs"] else None
            return self.reply(200, {"job": job})

        if self.path == "/agent/result":
            if not self.authed(AGENT_TOKEN):
                return self.reply(401, {"error": "bad token"})
            b = self.body()
            with lock:
                p = pending.get(b.get("id"))
                if p:
                    p["result"] = b
                    p["cv"].notify_all()
            return self.reply(200, {"ok": True})

        m = re.fullmatch(r"/devices/([A-Za-z0-9_-]{1,32})/run", self.path)
        if not m:
            return self.reply(404, {"error": "not found"})
        if not self.authed(CLIENT_TOKEN):
            return self.reply(401, {"error": "bad token"})
        b = self.body()
        alias = m.group(1)
        job = {"id": uuid.uuid4().hex, "action": str(b.get("action", "")), "args": [str(x) for x in b.get("args", [])][:8]}
        with lock:
            s = agents.get(alias)
            if not s or time.time() - s["last"] > 40:
                return self.reply(404, {"error": "device offline or unknown"})
            p = pending[job["id"]] = {"cv": threading.Condition(lock), "result": None}
            s["jobs"].append(job)
            s["cv"].notify_all()
            p["cv"].wait_for(lambda: p["result"] is not None, timeout=90)
            res = pending.pop(job["id"])["result"]
        if res is None:
            return self.reply(504, {"error": "device did not answer in time"})
        res.pop("id", None)
        print(time.strftime("%F %T"), "job", alias, job["action"], "ok" if res.get("ok") else "failed", flush=True)
        self.reply(200, res)

class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):  # default prints the client IP: say nothing
        pass

srv = Server(("0.0.0.0", 443), Handler)
srv.daemon_threads = True
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(os.path.join(DIR, "tls.crt"), os.path.join(DIR, "tls.key"))
# handshake inside each handler thread, so a slow or bogus client cannot block everyone else
srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
print("relay listening on 443", flush=True)
srv.serve_forever()
