#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "pyyaml"]
# ///
"""llama-jockey — CLI to control llama-server remotely."""

import argparse
import hashlib
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
import urllib3
import urllib3.exceptions
import yaml
from requests.adapters import HTTPAdapter


CONFIG_FILE = Path(__file__).parent / "llama-jockey.yaml"

SAMPLE_CONFIG = "cp llama-jockey.example.yaml llama-jockey.yaml"


def load_config():
    if not CONFIG_FILE.exists():
        print(f"Config not found: {CONFIG_FILE}")
        print("Create it with:\n")
        print(SAMPLE_CONFIG)
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def save_fingerprint(fingerprint):
    cfg = load_config()
    cfg["llama"]["trusted_cert"] = fingerprint
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)


class FingerprintAdapter(HTTPAdapter):
    """Bypass CA validation but enforce SHA-256 fingerprint at the urllib3 level."""
    def __init__(self, fingerprint):
        self._fp = fingerprint.split(":", 1)[1] if ":" in fingerprint else fingerprint
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        kwargs["assert_fingerprint"] = self._fp
        super().init_poolmanager(*args, **kwargs)


def get_server_fingerprint(url):
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 443
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=5) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            cert_der = ssock.getpeercert(binary_form=True)
    return "sha256:" + hashlib.sha256(cert_der).hexdigest()


class LlamaClient:
    class Unreachable(Exception):
        pass

    class APIError(Exception):
        def __init__(self, status, body):
            self.status = status
            self.body = body

        def __str__(self):
            return f"HTTP {self.status}: {self.body}"

    def __init__(self, cfg):
        self.name = cfg["llama"].get("name", "llama-server")
        self.base = cfg["llama"]["url"].rstrip("/")
        self.headers = {"Authorization": f"Bearer {cfg['llama']['api_key']}"}
        self._trusted_fp = cfg["llama"].get("trusted_cert")
        self._session = requests.Session()
        if self._trusted_fp:
            self._mount_fingerprint_adapter(self._trusted_fp)

    def _mount_fingerprint_adapter(self, fingerprint):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._session.verify = False
        self._session.mount("https://", FingerprintAdapter(fingerprint))

    def _do(self, method, path, **kwargs):
        url = f"{self.base}{path}"
        try:
            r = self._session.request(method, url, headers=self.headers, **kwargs)
        except requests.exceptions.SSLError:
            self._handle_ssl_trust()
            r = self._session.request(method, url, headers=self.headers, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            raise LlamaClient.Unreachable()
        if not r.ok:
            raise LlamaClient.APIError(r.status_code, r.text)
        return r.json()

    def _handle_ssl_trust(self):
        try:
            fp = get_server_fingerprint(self.base)
        except (socket.error, ssl.SSLError):
            raise LlamaClient.Unreachable()

        if self._trusted_fp:
            print(f"Certificate fingerprint mismatch for {self.name}!")
            print(f"  Expected: {self._trusted_fp}")
            print(f"  Got:      {fp}")
            print("Aborting — possible MITM attack.")
            sys.exit(1)

        print(f"Untrusted certificate from {self.name}")
        print(f"  Fingerprint: {fp}")
        answer = input("Trust this certificate? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(1)
        save_fingerprint(fp)
        self._trusted_fp = fp
        self._mount_fingerprint_adapter(fp)
        print("Fingerprint saved to llama-jockey.yaml.")

    def get(self, path):
        return self._do("GET", path, timeout=5)

    def post(self, path, body=None):
        return self._do("POST", path, json=body, timeout=30)

    def list_models(self):
        data = self.get("/models")
        return data.get("data", [])

    def load(self, model):
        return self.post("/models/load", {"model": model})

    def unload(self, model):
        return self.post("/models/unload", {"model": model})

    def unload_all(self):
        models = self.list_models()
        for m in models:
            try:
                self.unload(m["id"])
            except LlamaClient.APIError:
                pass  # not loaded — ignore
        return len(models)


# ── CLI commands ──────────────────────────────────────────────────────────────


def cmd_models(llama):
    models = llama.list_models()
    if not models:
        print("No models found.")
        return
    for i, m in enumerate(models, 1):
        state = m.get("status", {}).get("value", "")
        tag = f" [{state}]" if state else ""
        print(f"  {i}. {m['id']}{tag}")


def resolve_model(llama, ref):
    if ref.isdigit():
        models = llama.list_models()
        idx = int(ref) - 1
        if idx < 0 or idx >= len(models):
            print(f"No model #{ref}.")
            sys.exit(1)
        return models[idx]["id"]
    return ref


def cmd_load(llama, model):
    model = resolve_model(llama, model)
    llama.load(model)
    print(f"Loaded: {model}")


def cmd_unload(llama, model):
    if model:
        model = resolve_model(llama, model)
        llama.unload(model)
        print(f"Unloaded: {model}")
    else:
        n = llama.unload_all()
        print(f"Unloaded {n} model(s).")


def cmd_chat(llama, prompt):
    models = llama.list_models()
    active = next(
        (m for m in models if m.get("status", {}).get("value") in ("loaded", "sleeping")),
        None,
    )
    if not active:
        print("No model is loaded or sleeping.")
        sys.exit(1)
    result = llama.post(
        "/v1/chat/completions",
        {"model": active["id"], "messages": [{"role": "user", "content": prompt}], "stream": False},
    )
    print(result["choices"][0]["message"]["content"])


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Control llama-server remotely.")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("models", help="List available models")

    p_load = sub.add_parser("load", help="Load a model by name")
    p_load.add_argument("model")

    p_unload = sub.add_parser("unload", help="Unload a model, or all if no arg given")
    p_unload.add_argument("model", nargs="?", default=None)

    p_chat = sub.add_parser("chat", help="Send a one-shot chat prompt")
    p_chat.add_argument("prompt")

    args = parser.parse_args()
    cfg = load_config()
    llama = LlamaClient(cfg)

    try:
        if args.cmd == "models":
            cmd_models(llama)
        elif args.cmd == "load":
            cmd_load(llama, args.model)
        elif args.cmd == "unload":
            cmd_unload(llama, args.model)
        elif args.cmd == "chat":
            cmd_chat(llama, args.prompt)
    except LlamaClient.Unreachable:
        print(f"Cannot reach {llama.name}.")
        sys.exit(1)
    except LlamaClient.APIError as e:
        print(f"API error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
