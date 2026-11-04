#!/usr/bin/env python3
"""On-demand, token-protected two-way sharing for a private LAN."""
from __future__ import annotations

import argparse
import cgi
import html
import ipaddress
import json
import mimetypes
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MIB = 1024 * 1024


def private_ipv4_addresses() -> list[str]:
    """Return usable local IPv4 addresses without a network dependency."""
    found: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            found.add(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(sockaddr[0])
    except socket.gaierror:
        pass
    return sorted(ip for ip in found if ipaddress.ip_address(ip).is_private and not ip.startswith("127."))


def safe_name(name: str, fallback: str = "upload") -> str:
    name = Path(name).name.replace("\x00", "").strip().replace("/", "_").replace("\\", "_")
    name = "".join(c if c.isalnum() or c in " ._-()[]" else "_" for c in name).strip(" .")
    return (name or fallback)[:180]


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / safe_name(filename)
    stem, suffix = candidate.stem, candidate.suffix
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({index}){suffix}"
        index += 1
    return candidate


def zip_folder(folder: Path, temp_dir: Path) -> Path:
    target = unique_path(temp_dir, f"{safe_name(folder.name, 'folder')}.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in folder.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(folder.parent))
    return target


def clipboard_text() -> str | None:
    for command in (("wl-paste", "--no-newline"), ("xclip", "-selection", "clipboard", "-o"), ("xsel", "--clipboard", "--output")):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=True)
            return result.stdout
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
    return None


def screenshot(temp_dir: Path) -> Path:
    target = temp_dir / f"screenshot-{datetime.now():%Y%m%d-%H%M%S}.png"
    for command in (("gnome-screenshot", "-f", str(target)), ("grim", str(target))):
        try:
            subprocess.run(command, check=True, timeout=20)
            if target.is_file():
                return target
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
    raise RuntimeError("No screenshot tool found. Install/use gnome-screenshot or grim, then try again.")


class ShareState:
    def __init__(self, args: argparse.Namespace, bind_ip: str, temp_dir: Path):
        self.token = secrets.token_urlsafe(32)
        self.bind_ip = bind_ip
        self.started = time.monotonic()
        self.minutes = args.minutes
        self.max_upload = args.max_upload_mib * MIB
        self.temp_dir = temp_dir
        self.inbox = Path(args.inbox).expanduser() / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.downloads: list[Path] = []
        self.text_items: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    def expired(self) -> bool:
        return self.minutes > 0 and time.monotonic() - self.started > self.minutes * 60

    def add_path(self, value: str) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Not found: {path}")
        if path.is_dir():
            path = zip_folder(path, self.temp_dir)
        if not path.is_file():
            raise ValueError(f"Not a regular file: {path}")
        with self.lock:
            self.downloads.append(path)
        return path


class QuickShareServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, handler, state: ShareState):
        super().__init__(address, handler)
        self.state = state


class Handler(BaseHTTPRequestHandler):
    server: QuickShareServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {fmt % args}")

    def client_is_safe(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_private
        except ValueError:
            return False

    def session_path(self) -> str:
        return f"/s/{self.server.state.token}"

    def authorized(self) -> bool:
        return self.client_is_safe() and (self.path == self.session_path() or self.path.startswith(self.session_path() + "/"))

    def deny(self, status=HTTPStatus.NOT_FOUND, text="Not found") -> None:
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def send_html(self, content: str, status=HTTPStatus.OK) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers(); self.wfile.write(body)

    def do_GET(self) -> None:
        state = self.server.state
        if state.expired(): return self.deny(HTTPStatus.GONE, "This QuickShare session has expired.")
        if not self.authorized(): return self.deny()
        path = urllib.parse.urlsplit(self.path).path
        base = self.session_path()
        if path == base: return self.send_html(self.page())
        if path.startswith(base + "/download/"):
            try: index = int(path.rsplit("/", 1)[1]); file = state.downloads[index]
            except (ValueError, IndexError): return self.deny()
            return self.download(file)
        return self.deny()

    def download(self, file: Path) -> None:
        try:
            size = file.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(file.name)}")
            self.send_header("Content-Length", str(size)); self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with file.open("rb") as source: shutil.copyfileobj(source, self.wfile)
        except (OSError, BrokenPipeError): pass

    def do_POST(self) -> None:
        state = self.server.state
        if state.expired(): return self.deny(HTTPStatus.GONE, "This QuickShare session has expired.")
        if not self.authorized(): return self.deny()
        path = urllib.parse.urlsplit(self.path).path
        if path == self.session_path() + "/upload": return self.receive_upload()
        if path == self.session_path() + "/stop":
            self.send_html("<script>window.close()</script><p>Sharing stopped. You may close this tab.</p>")
            threading.Thread(target=self.stop_later, daemon=True).start(); return
        return self.deny()

    def stop_later(self):
        time.sleep(.2); self.server.state.stopping.set(); self.server.shutdown()

    def receive_upload(self) -> None:
        state = self.server.state
        try: length = int(self.headers.get("Content-Length", "0"))
        except ValueError: return self.deny(HTTPStatus.BAD_REQUEST, "Invalid request length")
        if length <= 0 or length > state.max_upload: return self.deny(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Upload exceeds this session's limit.")
        if not self.headers.get_content_type() == "multipart/form-data": return self.deny(HTTPStatus.BAD_REQUEST, "Expected a form upload")
        environ = {"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers["Content-Type"], "CONTENT_LENGTH": str(length)}
        try: form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=environ, keep_blank_values=True)
        except Exception: return self.deny(HTTPStatus.BAD_REQUEST, "Could not read upload")
        saved = []
        text_value = form.getfirst("message", "").strip()
        if text_value:
            target = unique_path(state.inbox, "phone-text.txt")
            target.write_text(text_value, encoding="utf-8")
            saved.append(target.name)
        uploads = form["files"] if "files" in form else []
        if not isinstance(uploads, list): uploads = [uploads]
        for item in uploads:
            if not getattr(item, "filename", None): continue
            target = unique_path(state.inbox, item.filename)
            total = 0
            with target.open("xb") as dest:
                while chunk := item.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > state.max_upload:
                        dest.close(); target.unlink(missing_ok=True)
                        return self.deny(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "An uploaded file exceeds the limit.")
                    dest.write(chunk)
            saved.append(target.name)
        message = "Nothing was selected." if not saved else "Received: " + ", ".join(html.escape(x) for x in saved)
        self.send_html(f"<meta http-equiv='refresh' content='2;url={self.session_path()}'> <p>{message}</p><p>Returning to QuickShare…</p>")

    def page(self) -> str:
        state = self.server.state; base = self.session_path()
        links = "".join(f"<li><a href='{base}/download/{i}' download>{html.escape(p.name)}</a> <small>{p.stat().st_size / MIB:.1f} MiB</small></li>" for i, p in enumerate(state.downloads) if p.exists())
        if not links: links = "<li>No laptop files selected for download.</li>"
        return f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>QuickShare</title><style>body{{font-family:system-ui,sans-serif;max-width:680px;margin:2rem auto;padding:0 1rem;color:#172033}}section{{margin:1rem 0;padding:1rem;border:1px solid #d7dce5;border-radius:12px}}button,input{{font:inherit;margin:.4rem 0}}button{{padding:.55rem .85rem}}small{{color:#5c6678}}.warn{{color:#8a4300}}</style></head><body><h1>QuickShare</h1><p>Private, temporary LAN session. Stop when finished.</p><section><h2>Download from laptop</h2><ul>{links}</ul></section><section><h2>Send to laptop</h2><form method='post' action='{base}/upload' enctype='multipart/form-data'><label>Text<br><textarea name='message' rows='5' style='width:100%'></textarea></label><br><label>Files <input type='file' name='files' multiple></label><br><label>Folder <input type='file' name='files' multiple webkitdirectory></label><br><small>The folder picker appears only in browsers that support it; otherwise upload a ZIP.</small><br><button type='submit'>Send to laptop</button></form></section><section><form method='post' action='{base}/stop' onsubmit='return confirm("Stop sharing for every device?")'><button>Stop sharing</button></form><p class='warn'><small>Do not share this link on public Wi-Fi. Files sent to the laptop go to: {html.escape(str(state.inbox))}</small></p></section></body></html>"""


def print_qr(url: str) -> None:
    vendor = Path(__file__).with_name(".quickshare_vendor")
    if vendor.is_dir(): sys.path.insert(0, str(vendor))
    try:
        import qrcode
        qr = qrcode.QRCode(border=2); qr.add_data(url); qr.make(fit=True)
        print(qr.print_ascii(invert=True))
    except ImportError:
        print("QR package is not installed. Use the URL above, or see README.md for a project-local install command.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start a temporary two-way QuickShare LAN session.")
    parser.add_argument("paths", nargs="*", help="Files or folders to offer for download")
    parser.add_argument("--clipboard", action="store_true", help="Share current clipboard text")
    parser.add_argument("--screenshot", action="store_true", help="Take and share a screenshot")
    parser.add_argument("--pick", action="store_true", help="Open a graphical file picker when zenity is available")
    parser.add_argument("--bind", help="Private LAN IPv4 address to bind (use when more than one is shown)")
    parser.add_argument("--port", type=int, default=0, help="Port (default: random available port)")
    parser.add_argument("--minutes", type=int, default=60, help="Session timeout in minutes; 0 means manual stop")
    parser.add_argument("--max-upload-mib", type=int, default=500, help="Maximum total request upload size in MiB")
    parser.add_argument("--inbox", default="~/QuickShare-Inbox", help="Where incoming uploads are saved")
    args = parser.parse_args()
    if args.minutes < 0 or args.max_upload_mib < 1 or not 0 <= args.port <= 65535: parser.error("Invalid limit or port")
    addresses = private_ipv4_addresses()
    bind_ip = args.bind or (addresses[0] if len(addresses) == 1 else None)
    if not bind_ip:
        parser.error("Could not select a private LAN address. Use --bind <your Wi-Fi IPv4>. Found: " + ", ".join(addresses or ["none"]))
    try:
        if not ipaddress.ip_address(bind_ip).is_private: parser.error("--bind must be a private IPv4 address")
    except ValueError: parser.error("--bind must be an IPv4 address")
    with tempfile.TemporaryDirectory(prefix="quickshare-") as work:
        state = ShareState(args, bind_ip, Path(work))
        for value in args.paths: state.add_path(value)
        if args.clipboard:
            clip = clipboard_text()
            if clip is None: print("Clipboard reader not found; continuing without clipboard text.", file=sys.stderr)
            elif clip: state.text_items.append(("Laptop clipboard", clip))
        if args.screenshot: state.add_path(str(screenshot(state.temp_dir)))
        if args.pick:
            try:
                result = subprocess.run(("zenity", "--file-selection", "--multiple", "--separator=|"), capture_output=True, text=True, check=False)
                for value in filter(None, result.stdout.strip().split("|")): state.add_path(value)
            except FileNotFoundError: print("zenity is unavailable; use paths on the command line.", file=sys.stderr)
        if state.text_items:
            text_file = state.temp_dir / "laptop-clipboard.txt"; text_file.write_text(state.text_items[0][1], encoding="utf-8"); state.downloads.append(text_file)
        server = QuickShareServer((bind_ip, args.port), Handler, state)
        url = f"http://{bind_ip}:{server.server_port}/s/{state.token}"
        print("\nQuickShare is running (no background service).")
        print("Android URL:", url)
        print("Incoming uploads:", state.inbox)
        print_qr(url)
        print("Press Ctrl+C to stop sharing.\n")
        expiry_timer = None
        if args.minutes:
            expiry_timer = threading.Timer(args.minutes * 60, server.shutdown)
            expiry_timer.daemon = True
            expiry_timer.start()
        try:
            webbrowser.open(url)
            server.serve_forever(poll_interval=.5)
        except KeyboardInterrupt: print("\nStopping QuickShare…")
        finally: server.server_close()
        if expiry_timer:
            expiry_timer.cancel()
    print("QuickShare stopped. Uploaded files remain in the inbox shown above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
