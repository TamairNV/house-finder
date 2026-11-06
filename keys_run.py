#!/usr/bin/env python3
"""Start an app with its keys from the vault: python keys_run.py <command> [args...]

Fetches the keys, sets them as environment variables and starts the command. Checks every few
minutes and restarts the command if a key changed, so rolling a key in the vault is all it takes.
Standard library only, and each app keeps its own copy of this file (see client/README.md).

  KEYS_URL            http://keys:8001 (unset: just runs the command)
  KEYS_APP_TOKEN      this app's token (unset: just runs the command)
  KEYS_CACHE_FILE     saved copy, used if the vault is down when the app starts
  KEYS_POLL_SECONDS   default 300
  KEYS_RETRY_SECONDS  default 2
  KEYS_START_TRIES    tries before falling back to the saved copy, default 5
"""
import http.client
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request

STOP_GRACE_SECONDS = 20


class Revoked(Exception):
    pass


class Unreachable(Exception):
    pass


def log(msg):
    print(f"keys_run: {msg}", file=sys.stderr, flush=True)


def get(url, token, path):
    req = urllib.request.Request(url.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Revoked() from None
        raise Unreachable(f"HTTP {e.code}") from None
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise Unreachable(type(e).__name__) from None


def read_cache(path):
    if not path:
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data.get("values"), dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def write_cache(path, data):
    if not path:
        return
    tmp = path + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"version": data["version"], "values": data["values"]}, f)
        os.replace(tmp, path)
    except OSError as e:
        log(f"couldn't save a copy of the keys: {type(e).__name__}")


def drop_cache(path):
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def fetch(cfg):
    data = get(cfg["url"], cfg["token"], "/v1/secrets")
    if data.get("missing"):
        log("no value in the vault yet for: " + ", ".join(data["missing"]))
    write_cache(cfg["cache"], data)
    return data


class Runner:
    def __init__(self, cfg, command):
        self.cfg, self.command = cfg, command
        self.stop = None
        self.pid = None
        signal.signal(signal.SIGTERM, self.on_signal)
        signal.signal(signal.SIGINT, self.on_signal)

    def on_signal(self, signum, _frame):
        self.stop = signum
        if self.pid:
            try:
                os.kill(self.pid, signum)
            except OSError:
                pass

    def first_load(self):
        saved = read_cache(self.cfg["cache"])
        tries = 0
        while not self.stop:
            try:
                return fetch(self.cfg)
            except Revoked:
                drop_cache(self.cfg["cache"])
                log("the vault refused this app's token, not starting")
                sys.exit(78)
            except Unreachable as e:
                tries += 1
                if saved and tries >= self.cfg["tries"]:
                    log(f"vault unreachable ({e}), starting from the saved copy")
                    return saved
                if tries == 1 or tries % 30 == 0:
                    log(f"vault unreachable ({e}), still trying")
                time.sleep(self.cfg["retry"])
        sys.exit(128 + self.stop)

    def spawn(self, data):
        env = {k: v for k, v in os.environ.items() if not k.startswith("KEYS_")}
        env.update(data["values"])
        self.pid = os.spawnvpe(os.P_NOWAIT, self.command[0], self.command, env)

    def reap(self):
        """Collect finished processes (as PID 1 we inherit orphans too). The child's exit code, or None."""
        code = None
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return code
            if pid == 0:
                return code
            if pid == self.pid:
                self.pid = None
                code = os.waitstatus_to_exitcode(status)

    def stop_child(self):
        os.kill(self.pid, signal.SIGTERM)
        end = time.monotonic() + STOP_GRACE_SECONDS
        while self.pid and time.monotonic() < end:
            self.reap()
            time.sleep(0.05)
        if self.pid:
            os.kill(self.pid, signal.SIGKILL)
            while self.pid:
                self.reap()
                time.sleep(0.05)

    def check_for_change(self, version):
        try:
            if get(self.cfg["url"], self.cfg["token"], "/v1/version").get("version") == version:
                return version
            data = fetch(self.cfg)
        except Revoked:
            drop_cache(self.cfg["cache"])
            log("the vault refused this app's token; still running, but it won't start again")
            return version
        except Unreachable:
            return version
        log("a key changed, restarting the app")
        self.stop_child()
        if not self.stop:
            self.spawn(data)
        return data["version"]

    def run(self):
        data = self.first_load()
        version = data.get("version")
        self.spawn(data)
        next_check = time.monotonic() + self.cfg["poll"]
        while True:
            code = self.reap()
            if code is not None:
                return 128 - code if code < 0 else code
            if not self.pid:  # stopped by a signal while restarting
                return 128 + (self.stop or 0)
            if not self.stop and time.monotonic() >= next_check:
                version = self.check_for_change(version)
                next_check = time.monotonic() + self.cfg["poll"]
            time.sleep(0.1)


def main(argv):
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    env = os.environ
    if not (env.get("KEYS_URL") and env.get("KEYS_APP_TOKEN")):
        log("KEYS_URL or KEYS_APP_TOKEN not set, running without the vault")
        os.execvp(argv[0], argv)
    cfg = {
        "url": env["KEYS_URL"], "token": env["KEYS_APP_TOKEN"], "cache": env.get("KEYS_CACHE_FILE"),
        "poll": float(env.get("KEYS_POLL_SECONDS", 300)), "retry": float(env.get("KEYS_RETRY_SECONDS", 2)),
        "tries": int(env.get("KEYS_START_TRIES", 5)),
    }
    return Runner(cfg, argv).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
