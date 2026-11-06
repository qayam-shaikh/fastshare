#!/usr/bin/env python3
"""A temporary, token-protected Ubuntu-to-Android QuickShare web app."""
from __future__ import annotations

import argparse
import base64
import cgi
import html
import ipaddress
import mimetypes
import secrets
import shutil
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
from io import BytesIO
from pathlib import Path

MIB = 1024 * 1024


def private_ipv4_addresses() -> list[str]:
    found: set[str] = set()
    preferred: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            preferred.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        found.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except socket.gaierror:
        pass
    usable = {ip for ip in found | set(preferred) if ipaddress.ip_address(ip).is_private and not ip.startswith("127.")}
    # Keep the default-route address first. Sorting alone can wrongly select a
    # Docker/virtual-machine network that an Android phone cannot reach.
    return [ip for ip in preferred if ip in usable] + sorted(usable - set(preferred))


def safe_name(name: str, fallback: str = "upload") -> str:
    name = Path(name).name.replace("\x00", "").strip()
    name = "".join(char if char.isalnum() or char in " ._-()[]" else "_" for char in name).strip(" .")
    return (name or fallback)[:180]


def safe_relative_path(name: str) -> Path:
    parts = [safe_name(part) for part in name.replace("\\", "/").split("/") if part not in ("", ".", "..")]
    return Path(*parts) if parts else Path("upload")


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / safe_name(filename)
    stem, suffix = candidate.stem, candidate.suffix
    count = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({count}){suffix}"
        count += 1
    return candidate


def clipboard_text() -> str | None:
    for command in (("wl-paste", "--no-newline"), ("xclip", "-selection", "clipboard", "-o"), ("xsel", "--clipboard", "--output")):
        try:
            return subprocess.run(command, capture_output=True, text=True, timeout=3, check=True).stdout
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
    return None


class ShareState:
    def __init__(self, args: argparse.Namespace, bind_ip: str, work: Path):
        self.admin_token = secrets.token_urlsafe(32)
        self.phone_token: str | None = None
        self.bind_ip = bind_ip
        self.started = time.monotonic()
        self.minutes = args.minutes
        self.max_upload = args.max_upload_mib * MIB
        self.work = work
        self.selection_dir = work / "selected"
        self.selection_dir.mkdir(parents=True)
        self.inbox = Path(args.inbox).expanduser() / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.downloads: list[Path] = []
        self.received: list[str] = []
        self.selection_label = ""
        self.mode: str | None = None
        self.screen = "home"
        self.lock = threading.Lock()

    def expired(self) -> bool:
        return self.minutes > 0 and time.monotonic() - self.started > self.minutes * 60

    def start_phone_session(self, mode: str) -> None:
        with self.lock:
            self.mode = mode
            self.phone_token = secrets.token_urlsafe(32)

    def clear_selection(self) -> None:
        self.downloads.clear()
        self.selection_label = ""
        shutil.rmtree(self.selection_dir, ignore_errors=True)
        self.selection_dir.mkdir()

    def add_clipboard(self) -> None:
        text = clipboard_text()
        if text is None:
            raise ValueError("Clipboard access is unavailable. Install wl-clipboard, xclip, or xsel.")
        target = self.selection_dir / "clipboard.txt"
        target.write_text(text, encoding="utf-8")
        self.downloads = [target]
        self.selection_label = "Clipboard text"

    def save_uploads(self, form: cgi.FieldStorage, field: str, *, folder: bool = False) -> None:
        items = form[field] if field in form else []
        if not isinstance(items, list):
            items = [items]
        target_dir = self.selection_dir / ("folder" if folder else "files")
        target_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for item in items:
            if not getattr(item, "filename", None):
                continue
            target = target_dir / safe_relative_path(item.filename) if folder else unique_path(target_dir, item.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as dest:
                while chunk := item.file.read(1024 * 1024):
                    dest.write(chunk)
            saved += 1
        if not saved:
            raise ValueError("Choose at least one item first.")
        if folder:
            archive = self.selection_dir / "folder.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for file in target_dir.rglob("*"):
                    if file.is_file():
                        zip_file.write(file, file.relative_to(target_dir))
            self.downloads = [archive]
            self.selection_label = "Folder (ZIP)"
        else:
            self.downloads = [path for path in target_dir.rglob("*") if path.is_file()]
            self.selection_label = f"{saved} selected item{'s' if saved != 1 else ''}"


class QuickShareServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, state: ShareState):
        super().__init__(address, Handler)
        self.state = state


class Handler(BaseHTTPRequestHandler):
    server: QuickShareServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {fmt % args}")

    def is_private_client(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_private
        except ValueError:
            return False

    def is_laptop(self) -> bool:
        return self.client_address[0] == self.server.state.bind_ip or self.client_address[0] in ("127.0.0.1", "::1")

    def admin_path(self) -> str:
        return f"/app/{self.server.state.admin_token}"

    def phone_path(self) -> str:
        return f"/share/{self.server.state.phone_token or ''}"

    def is_admin(self, path: str) -> bool:
        return self.is_laptop() and (path == self.admin_path() or path.startswith(self.admin_path() + "/"))

    def is_phone(self, path: str) -> bool:
        # The server itself binds only to the chosen private LAN address. Some
        # Wi-Fi/router setups present a phone with an address Python does not
        # classify as private, so access is controlled by the unguessable
        # per-session QR token rather than client-address classification.
        return self.server.state.phone_token is not None and (path == self.phone_path() or path.startswith(self.phone_path() + "/"))

    def send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers(); self.wfile.write(body)

    def send_html(self, text: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_text(text, status, "text/html; charset=utf-8")

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def deny(self, status: HTTPStatus = HTTPStatus.NOT_FOUND, message: str = "Not found") -> None:
        self.send_text(message, status)

    def do_GET(self) -> None:
        state = self.server.state
        if state.expired(): return self.deny(HTTPStatus.GONE, "This QuickShare session has expired.")
        path = urllib.parse.urlsplit(self.path).path
        if self.is_admin(path): return self.send_html(self.admin_page())
        if self.is_phone(path):
            suffix = path[len(self.phone_path()):]
            if suffix.startswith("/download/"): return self.download(suffix.rsplit("/", 1)[1])
            if suffix in ("", "/"): return self.send_html(self.phone_page())
        return self.deny()

    def do_POST(self) -> None:
        state = self.server.state
        if state.expired(): return self.deny(HTTPStatus.GONE, "This QuickShare session has expired.")
        path = urllib.parse.urlsplit(self.path).path
        if self.is_admin(path): return self.handle_admin_post(path[len(self.admin_path()):])
        if self.is_phone(path) and path == self.phone_path() + "/upload":
            if state.mode != "receive": return self.deny()
            return self.receive_from_phone()
        return self.deny()

    def parse_form(self) -> cgi.FieldStorage | None:
        try: length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.deny(HTTPStatus.BAD_REQUEST, "Invalid request length"); return None
        if length <= 0 or length > self.server.state.max_upload:
            self.deny(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "This request exceeds the upload limit."); return None
        if self.headers.get_content_type() != "multipart/form-data":
            self.deny(HTTPStatus.BAD_REQUEST, "Expected a form upload."); return None
        environ = {"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers["Content-Type"], "CONTENT_LENGTH": str(length)}
        try: return cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=environ, keep_blank_values=True)
        except Exception:
            self.deny(HTTPStatus.BAD_REQUEST, "Could not read upload."); return None

    def handle_admin_post(self, suffix: str) -> None:
        state = self.server.state
        if suffix == "/receive":
            state.clear_selection(); state.start_phone_session("receive")
            # Do not leave the browser on this POST URL: the live-activity
            # refresh would resubmit it and invalidate the QR every 2 seconds.
            return self.redirect(self.admin_path())
        if suffix == "/open-send":
            state.clear_selection(); state.phone_token = None; state.mode = None; state.screen = "send"
            return self.send_html(self.admin_page())
        if suffix == "/home":
            state.clear_selection(); state.phone_token = None; state.mode = None; state.screen = "home"
            return self.send_html(self.admin_page())
        if suffix == "/clipboard":
            try: state.clear_selection(); state.add_clipboard()
            except ValueError as error: return self.send_html(self.admin_page(str(error)), HTTPStatus.BAD_REQUEST)
            return self.send_html(self.admin_page())
        if suffix in ("/images", "/files", "/folder"):
            form = self.parse_form()
            if form is None: return
            try:
                state.clear_selection()
                state.save_uploads(form, "items" if suffix != "/folder" else "folderitems", folder=suffix == "/folder")
            except ValueError as error: return self.send_html(self.admin_page(str(error)), HTTPStatus.BAD_REQUEST)
            return self.send_html(self.admin_page())
        if suffix == "/send":
            if not state.downloads: return self.send_html(self.admin_page("Choose something to send first."), HTTPStatus.BAD_REQUEST)
            state.start_phone_session("send")
            return self.send_html(self.admin_page())
        if suffix == "/stop":
            self.send_html("<p>QuickShare stopped. You can close this tab.</p>")
            threading.Thread(target=self.stop_later, daemon=True).start(); return
        return self.deny()

    def stop_later(self) -> None:
        time.sleep(.2); self.server.shutdown()

    def receive_from_phone(self) -> None:
        form = self.parse_form()
        if form is None: return
        state = self.server.state; saved: list[str] = []
        text = form.getfirst("message", "").strip()
        if text:
            target = unique_path(state.inbox, "phone-text.txt")
            target.write_text(text, encoding="utf-8"); saved.append(target.name)
            state.received.append(f"Text: {text[:500]}")
        items = form["files"] if "files" in form else []
        if not isinstance(items, list): items = [items]
        for item in items:
            if not getattr(item, "filename", None): continue
            target = unique_path(state.inbox, item.filename)
            with target.open("xb") as dest:
                while chunk := item.file.read(1024 * 1024): dest.write(chunk)
            saved.append(target.name)
            state.received.append(f"File: {target.name}")
        message = "Nothing was selected." if not saved else "Received: " + ", ".join(html.escape(name) for name in saved)
        self.send_html(f"<meta http-equiv='refresh' content='2;url={self.phone_path()}'> <p>{message}</p><p>Returning to QuickShare…</p>")

    def download(self, raw_index: str) -> None:
        if self.server.state.mode != "send": return self.deny()
        try: file = self.server.state.downloads[int(raw_index)]
        except (ValueError, IndexError): return self.deny()
        try:
            size = file.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(file.name)}")
            self.send_header("Content-Length", str(size)); self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with file.open("rb") as source: shutil.copyfileobj(source, self.wfile)
        except (OSError, BrokenPipeError): pass

    def qr_data_uri(self) -> str:
        try:
            vendor = Path(__file__).with_name(".quickshare_vendor")
            if vendor.is_dir(): sys.path.insert(0, str(vendor))
            import qrcode
            image = qrcode.make(self.phone_url())
            output = BytesIO(); image.save(output, format="PNG")
            return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        except ImportError: return ""

    def phone_url(self) -> str:
        return f"http://{self.server.state.bind_ip}:{self.server.server_port}{self.phone_path()}"

    def layout(self, content: str, title: str = "QuickShare") -> str:
        return f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title><style>:root{{color-scheme:light}}body{{background:#f5f7fb;color:#172033;font-family:system-ui,sans-serif;max-width:740px;margin:0 auto;padding:32px 18px}}h1{{margin:0 0 6px}}.sub{{color:#596579;margin:0 0 24px}}section{{background:white;border:1px solid #dde3ed;border-radius:16px;padding:20px;margin:14px 0;box-shadow:0 2px 8px #2030500c}}.choices{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}button,.button{{background:#1955d6;color:white;border:0;border-radius:10px;padding:12px 15px;font:inherit;font-weight:650;cursor:pointer;text-decoration:none;display:inline-block}}button.secondary,.button.secondary{{background:#eef2fb;color:#24477e}}input,textarea{{font:inherit;margin:8px 0;max-width:100%}}textarea{{width:100%;box-sizing:border-box}}small,.hint{{color:#596579}}.danger{{background:#a92a2a}}.error{{color:#a72b2b;background:#fff0f0;padding:10px;border-radius:8px}}.qr{{width:min(280px,90vw);image-rendering:pixelated;background:white;padding:12px;border-radius:12px}}ul{{padding-left:22px}}</style></head><body>{content}</body></html>"""

    def admin_page(self, error: str = "") -> str:
        state = self.server.state; base = self.admin_path()
        notice = f"<p class='error'>{html.escape(error)}</p>" if error else ""
        if state.phone_token:
            action = "Receive from phone" if state.mode == "receive" else f"Send {html.escape(state.selection_label)} to phone"
            qr = self.qr_data_uri()
            qr_html = f"<img class='qr' src='{qr}' alt='Scan this QR code with your phone'>" if qr else f"<p><a href='{html.escape(self.phone_url())}'>{html.escape(self.phone_url())}</a></p>"
            activity = ""
            if state.mode == "receive":
                items = "".join(f"<li>{html.escape(item)}</li>" for item in reversed(state.received)) or "<li>Waiting for your phone…</li>"
                activity = f"<section><h2>Received</h2><ul>{items}</ul><p class='hint'>This updates automatically.</p></section><script>setTimeout(() => location.reload(), 2000)</script>"
            return self.layout(f"<h1>QuickShare</h1><p class='sub'>{action}</p><section><h2>Scan with your phone</h2>{qr_html}<p class='hint'>Keep this page open while sharing.</p></section>{activity}<form method='post' action='{base}/stop'><button class='danger'>Stop sharing</button></form>")
        if state.screen == "home":
            return self.layout(f"""<h1>QuickShare</h1><p class='sub'>What do you want to do?</p>{notice}<div class='choices'><section><h2>Send</h2><p>Send something from this laptop to your phone.</p><form method='post' action='{base}/open-send'><button>Send to phone</button></form></section><section><h2>Receive</h2><p>Send something from your phone to this laptop.</p><form method='post' action='{base}/receive'><button>Receive from phone</button></form></section></div><form method='post' action='{base}/stop'><button class='danger'>Close app</button></form>""")
        selected = f"<p><strong>Ready:</strong> {html.escape(state.selection_label)}</p><form method='post' action='{base}/send'><button>Generate QR code</button></form>" if state.downloads else ""
        return self.layout(f"""<h1>QuickShare</h1><p class='sub'>Choose what to send from this laptop.</p>{notice}<section><div class='choices'><form method='post' action='{base}/clipboard'><button class='secondary'>Clipboard</button></form><form method='post' action='{base}/images' enctype='multipart/form-data'><label class='button secondary'>Images<input type='file' name='items' accept='image/*' multiple hidden onchange='this.form.submit()'></label></form><form method='post' action='{base}/files' enctype='multipart/form-data'><label class='button secondary'>Files<input type='file' name='items' multiple hidden onchange='this.form.submit()'></label></form><form method='post' action='{base}/folder' enctype='multipart/form-data'><label class='button secondary'>Folder<input type='file' name='folderitems' multiple webkitdirectory hidden onchange='this.form.submit()'></label></form></div><p class='hint'>Folders are converted to a ZIP file. {html.escape(state.selection_label)}</p>{selected}</section><form method='post' action='{base}/home'><button class='secondary'>Back</button></form><form method='post' action='{base}/stop'><button class='danger'>Close app</button></form>""")

    def phone_page(self) -> str:
        state = self.server.state; base = self.phone_path()
        if state.mode == "send":
            links = "".join(f"<li><a href='{base}/download/{i}' download>{html.escape(file.name)}</a> <small>{file.stat().st_size / MIB:.1f} MiB</small></li>" for i, file in enumerate(state.downloads) if file.exists())
            return self.layout(f"<h1>QuickShare</h1><p class='sub'>Download from laptop</p><section><ul>{links or '<li>Nothing is available.</li>'}</ul></section>")
        return self.layout(f"""<h1>QuickShare</h1><p class='sub'>Send to laptop</p><section><form method='post' action='{base}/upload' enctype='multipart/form-data'><label>Text<textarea name='message' rows='5' placeholder='Type or paste text'></textarea></label><br><label>Files<input type='file' name='files' multiple></label><br><label>Folder<input type='file' name='files' multiple webkitdirectory></label><p class='hint'>If the folder option is unavailable, upload a ZIP.</p><button>Send to laptop</button></form><p class='hint'>Items are saved in: {html.escape(str(state.inbox))}</p></section>""")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the temporary QuickShare web app.")
    parser.add_argument("--bind", help="Private LAN IPv4 address to bind")
    parser.add_argument("--port", type=int, default=0, help="Port (default: a random available port)")
    parser.add_argument("--minutes", type=int, default=60, help="Session timeout in minutes; 0 means manual stop")
    parser.add_argument("--max-upload-mib", type=int, default=500, help="Maximum upload request size in MiB")
    parser.add_argument("--inbox", default="~/QuickShare-Inbox", help="Where phone uploads are saved")
    args = parser.parse_args()
    if args.minutes < 0 or args.max_upload_mib < 1 or not 0 <= args.port <= 65535: parser.error("Invalid limit or port")
    addresses = private_ipv4_addresses()
    bind_ip = args.bind or (addresses[0] if len(addresses) == 1 else None)
    if not bind_ip: parser.error("Could not select a private LAN address. Use --bind <your Wi-Fi IPv4>. Found: " + ", ".join(addresses or ["none"]))
    try:
        if not ipaddress.ip_address(bind_ip).is_private: parser.error("--bind must be a private IPv4 address")
    except ValueError: parser.error("--bind must be an IPv4 address")
    with tempfile.TemporaryDirectory(prefix="quickshare-") as work:
        state = ShareState(args, bind_ip, Path(work))
        server = QuickShareServer((bind_ip, args.port), state)
        url = f"http://{bind_ip}:{server.server_port}/app/{state.admin_token}"
        print("QuickShare is running (no background service).\nLaptop app:", url, "\nPress Ctrl+C to stop.", flush=True)
        timer = threading.Timer(args.minutes * 60, server.shutdown) if args.minutes else None
        if timer: timer.daemon = True; timer.start()
        try:
            webbrowser.open(url); server.serve_forever(poll_interval=.5)
        except KeyboardInterrupt: print("\nStopping QuickShare…")
        finally:
            server.server_close()
            if timer: timer.cancel()
    print("QuickShare stopped. Phone uploads remain in ~/QuickShare-Inbox.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
