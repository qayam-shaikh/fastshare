#!/usr/bin/env python3
"""QuickShare LAN — A two-way, token-protected Ubuntu-to-Mobile file & text sharing web app."""
from __future__ import annotations

import argparse
import base64
import cgi
import html
import ipaddress
import json
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
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

MIB = 1024 * 1024
MAX_CLIPBOARD_CHARS = 2000

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico", ".jfif", ".tif", ".tiff"
}
ZIP_EXTENSIONS = {".zip"}


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < MIB:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024 * MIB:
        return f"{num_bytes / MIB:.1f} MB"
    return f"{num_bytes / (1024 * MIB):.2f} GB"


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
        found.update(
            item[4][0]
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        )
    except socket.gaierror:
        pass
    usable = {
        ip
        for ip in found | set(preferred)
        if ipaddress.ip_address(ip).is_private and not ip.startswith("127.")
    }
    return [ip for ip in preferred if ip in usable] + sorted(usable - set(preferred))




def get_system_mdns_hostname() -> str:
    raw = socket.gethostname().split(".")[0].strip()
    cleaned = "".join(c if c.isalnum() or c == "-" else "-" for c in raw).strip("-")
    return f"{cleaned or 'laptop'}.local"


class MdnsPublisher:
    """Publishes quickshare.local via avahi-publish on Ubuntu if available."""

    def __init__(self, bind_ip: str, alias: str = "quickshare.local") -> None:
        self.bind_ip = bind_ip
        self.mdns_host = get_system_mdns_hostname()
        self.alias = alias
        self.proc: subprocess.Popen | None = None
        self.active_hostname: str = get_system_mdns_hostname()

    def start(self) -> str:
        cmd = shutil.which("avahi-publish")
        if cmd:
            try:
                self.proc = subprocess.Popen(
                    [cmd, "-a", "-R", self.alias, self.bind_ip],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.15)
                if self.proc.poll() is None:
                    self.active_hostname = self.alias
                    return self.active_hostname
            except OSError:
                self.proc = None
        return self.active_hostname

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1.5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None


def safe_name(name: str, fallback: str = "upload") -> str:
    name = Path(name).name.replace("\x00", "").strip()
    name = "".join(
        char if char.isalnum() or char in " ._-()[]" else "_" for char in name
    ).strip(" .")
    return (name or fallback)[:180]


def safe_relative_path(name: str) -> Path:
    parts = [
        safe_name(part)
        for part in name.replace("\\", "/").split("/")
        if part not in ("", ".", "..")
    ]
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
    for command in (
        ("wl-paste", "--no-newline"),
        ("xclip", "-selection", "clipboard", "-o"),
        ("xsel", "--clipboard", "--output"),
    ):
        try:
            return subprocess.run(
                command, capture_output=True, text=True, timeout=3, check=True
            ).stdout
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
    return None


def classify_file_kind(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in ZIP_EXTENSIONS:
        return "zip"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        if guessed in ("application/zip", "application/x-zip-compressed"):
            return "zip"
        if guessed.startswith("image/"):
            return "image"
    return "file"


@dataclass
class TransferItem:
    id: str
    sender: str  # "laptop" or "phone"
    kind: str  # "text", "image", "zip", "file"
    name: str
    path: Path
    size: int
    mime: str
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%I:%M:%S %p"))
    text_content: str = ""
    text_too_long: bool = False
    is_folder_zip: bool = False

    def to_dict(self, base_url: str) -> dict:
        return {
            "id": self.id,
            "sender": self.sender,
            "kind": self.kind,
            "name": self.name,
            "size": self.size,
            "size_human": format_size(self.size),
            "mime": self.mime,
            "created_at": self.created_at,
            "text_content": self.text_content,
            "text_too_long": self.text_too_long,
            "is_folder_zip": self.is_folder_zip,
            "download_url": f"{base_url}/download/{self.id}",
            "preview_url": f"{base_url}/preview/{self.id}" if self.kind == "image" else "",
        }


class ShareState:
    def __init__(self, args: argparse.Namespace, bind_ip: str, work: Path):
        use_random = getattr(args, "random_token", False)
        self.admin_token = secrets.token_urlsafe(16) if use_random else ""
        # Fixed URL path by default so the phone URL stays identical across launches
        self.phone_token: str = secrets.token_urlsafe(16) if use_random else "" 
        self.bind_ip = bind_ip
        self.mdns_host = getattr(args, "mdns_host", "") or get_system_mdns_hostname()
        self.started = time.monotonic()
        self.minutes = args.minutes
        self.max_upload = args.max_upload_mib * MIB
        self.work = work
        self.storage_dir = work / "transfers"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.save_inbox = getattr(args, "save_inbox", False)
        self.inbox = (
            Path(args.inbox).expanduser() / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        )
        if self.save_inbox:
            self.inbox.mkdir(parents=True, exist_ok=True)

        self.items: list[TransferItem] = []
        self._counter = 0
        self.screen = "home"  # "home" or "workspace"
        self.initial_focus = "send"  # "send" or "receive"
        self.phone_connected = False
        self.last_phone_seen: float = 0.0
        self.lock = threading.Lock()

        # Retained for backwards compatibility
        self.downloads: list[Path] = []
        self.received: list[str] = []
        self.selection_label = ""
        self.mode: str | None = "both"

    def expired(self) -> bool:
        return self.minutes > 0 and time.monotonic() - self.started > self.minutes * 60

    def remaining_seconds(self) -> int:
        if self.minutes <= 0:
            return -1
        rem = int(self.minutes * 60 - (time.monotonic() - self.started))
        return max(0, rem)

    def mark_phone_seen(self) -> None:
        with self.lock:
            self.phone_connected = True
            self.last_phone_seen = time.monotonic()
            # Automatically transition laptop to active workspace once phone scans QR
            if self.screen == "home":
                self.screen = "workspace"

    def start_phone_session(self, mode: str = "both") -> None:
        with self.lock:
            self.mode = mode
            self.screen = "workspace"
            self.initial_focus = "receive" if mode == "receive" else "send" 

    def _next_id(self) -> str:
        self._counter += 1
        return f"item_{self._counter}_{secrets.token_hex(3)}"

    def get_item(self, item_id: str) -> TransferItem | None:
        with self.lock:
            for item in self.items:
                if item.id == item_id and item.path.exists():
                    return item
        return None

    def delete_item(self, item_id: str) -> bool:
        with self.lock:
            for idx, item in enumerate(self.items):
                if item.id == item_id:
                    try:
                        item.path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    self.items.pop(idx)
                    return True
        return False

    def add_text_item(self, sender: str, text: str, label: str = "text-snippet.txt") -> TransferItem:
        cleaned = text.replace("\r\n", "\n")
        if not cleaned.strip():
            raise ValueError("Enter or copy some non-empty text first.")
        with self.lock:
            item_id = self._next_id()
            target = unique_path(self.storage_dir, safe_name(label, "text.txt"))
            if not target.name.lower().endswith(".txt"):
                target = target.with_name(target.name + ".txt")
            target.write_text(cleaned, encoding="utf-8")
            if sender == "phone" and self.save_inbox:
                self.inbox.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, unique_path(self.inbox, target.name))

            size = target.stat().st_size
            too_long = len(cleaned) > MAX_CLIPBOARD_CHARS
            preview = cleaned[:600] + ("…" if len(cleaned) > 600 else "") if too_long else cleaned

            item = TransferItem(
                id=item_id,
                sender=sender,
                kind="text",
                name=target.name,
                path=target,
                size=size,
                mime="text/plain; charset=utf-8",
                text_content=preview,
                text_too_long=too_long,
            )
            self.items.append(item)
            if sender == "laptop":
                self.downloads.append(target)
                self.selection_label = "Text snippet"
                self.screen = "workspace"
            else:
                self.received.append(f"Text: {cleaned[:200]}")
            return item

    def add_clipboard(self) -> TransferItem:
        text = clipboard_text()
        if text is None or not text.strip():
            raise ValueError(
                "System clipboard is empty or unavailable. You can paste directly into the Text box instead, or install wl-clipboard / xclip / xsel."
            )
        return self.add_text_item("laptop", text, "clipboard.txt")

    def save_uploaded_items(
        self,
        sender: str,
        form: cgi.FieldStorage,
        field_name: str,
        *,
        folder: bool = False,
    ) -> list[TransferItem]:
        raw_items = form[field_name] if field_name in form else []
        if not isinstance(raw_items, list):
            raw_items = [raw_items]

        valid_items = [it for it in raw_items if getattr(it, "filename", None)]
        if not valid_items:
            raise ValueError("Choose at least one file or folder first.")

        created: list[TransferItem] = []

        if folder:
            with self.lock:
                batch_dir = self.storage_dir / f"folder_build_{ self._next_id() }"
            batch_dir.mkdir(parents=True, exist_ok=True)
            top_folder_name = "folder"
            saved_count = 0

            for item in valid_items:
                rel_path = safe_relative_path(item.filename)
                if len(rel_path.parts) > 1 and top_folder_name == "folder":
                    top_folder_name = rel_path.parts[0]
                target = batch_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as dest:
                    while chunk := item.file.read(MIB):
                        dest.write(chunk)
                saved_count += 1

            if saved_count == 0:
                shutil.rmtree(batch_dir, ignore_errors=True)
                raise ValueError("Selected folder was empty.")

            zip_filename = f"{safe_name(top_folder_name, 'folder')}.zip"
            with self.lock:
                archive_path = unique_path(self.storage_dir, zip_filename)
                with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for fpath in batch_dir.rglob("*"):
                        if fpath.is_file():
                            zf.write(fpath, fpath.relative_to(batch_dir))
                shutil.rmtree(batch_dir, ignore_errors=True)

                if sender == "phone" and self.save_inbox:
                    self.inbox.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(archive_path, unique_path(self.inbox, archive_path.name))

                item_id = self._next_id()
                transfer = TransferItem(
                    id=item_id,
                    sender=sender,
                    kind="zip",
                    name=archive_path.name,
                    path=archive_path,
                    size=archive_path.stat().st_size,
                    mime="application/zip",
                    is_folder_zip=True,
                )
                self.items.append(transfer)
                created.append(transfer)
                if sender == "laptop":
                    self.downloads.append(archive_path)
                    self.selection_label = f"Folder ({archive_path.name})"
                    self.screen = "workspace"
                else:
                    self.received.append(f"ZIP: {archive_path.name}")
            return created

        # Standard files or images
        for item in valid_items:
            raw_name = item.filename or "upload"
            with self.lock:
                target = unique_path(self.storage_dir, raw_name)
                with target.open("wb") as dest:
                    while chunk := item.file.read(MIB):
                        dest.write(chunk)

                if sender == "phone" and self.save_inbox:
                    self.inbox.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, unique_path(self.inbox, target.name))

                kind = classify_file_kind(target.name)
                mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                item_id = self._next_id()
                transfer = TransferItem(
                    id=item_id,
                    sender=sender,
                    kind=kind,
                    name=target.name,
                    path=target,
                    size=target.stat().st_size,
                    mime=mime,
                )
                self.items.append(transfer)
                created.append(transfer)
                if sender == "laptop":
                    self.downloads.append(target)
                    self.selection_label = f"{len(created)} item(s)"
                    self.screen = "workspace"
                else:
                    self.received.append(f"File: {target.name}")
        return created


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
        # Suppress noisy background state poll logs while keeping actual actions visible
        msg = fmt % args
        if "/api/state" in msg:
            return
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {msg}")

    def is_laptop(self) -> bool:
        return self.client_address[0] == self.server.state.bind_ip or self.client_address[0] in (
            "127.0.0.1",
            "::1",
        )

    def is_private_client(self) -> bool:
        try:
            addr = ipaddress.ip_address(self.client_address[0])
            return addr.is_private or addr.is_loopback
        except ValueError:
            return False

    def admin_path(self) -> str:
        tok = self.server.state.admin_token
        return f"/laptop/{tok}" if tok else "/laptop"

    def phone_path(self) -> str:
        tok = self.server.state.phone_token
        return f"/phone/{tok}" if tok else "/phone"

    def _normalize_alias_path(self, path: str) -> str:
        # Support both /laptop and legacy /app, and both /phone and legacy /share
        if path == "/app" or path.startswith("/app/"):
            return "/laptop" + path[len("/app"):]
        if path == "/share" or path.startswith("/share/"):
            return "/phone" + path[len("/share"):]
        return path

    def is_admin(self, path: str) -> bool:
        return self.is_laptop() and (
            path == self.admin_path() or path.startswith(self.admin_path() + "/")
        )

    def is_phone(self, path: str) -> bool:
        return (
            self.is_private_client()
            and self.server.state.phone_token is not None
            and (path == self.phone_path() or path.startswith(self.phone_path() + "/"))
        )

    def wants_json(self) -> bool:
        accept = self.headers.get("Accept", "")
        xhr = self.headers.get("X-Requested-With", "")
        return "application/json" in accept or xhr == "XMLHttpRequest"

    def send_text(
        self,
        text: str,
        status: HTTPStatus = HTTPStatus.OK,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_text(
            json.dumps(payload, ensure_ascii=False),
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def send_html(self, text: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_text(text, status, "text/html; charset=utf-8")

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def deny(
        self, status: HTTPStatus = HTTPStatus.NOT_FOUND, message: str = "Not found"
    ) -> None:
        if self.wants_json():
            self.send_json({"ok": False, "error": message}, status=status)
        else:
            self.send_text(message, status)

    def do_GET(self) -> None:
        state = self.server.state
        if state.expired():
            return self.deny(HTTPStatus.GONE, "This QuickShare session has expired.")
        path = urllib.parse.urlsplit(self.path).path
        while "//" in path:
            path = path.replace("//", "/")
        path = self._normalize_alias_path(path)

        if path.startswith("/static/"):
            return self.serve_static_asset(path[len("/static/"):])

        if path.startswith("/static/"):
            return self.serve_static_asset(path[len("/static/"):])

        if path in ("", "/"):
            return self.redirect(self.admin_path() if self.is_laptop() else self.phone_path())

        if self.is_admin(path):
            suffix = path[len(self.admin_path()) :]
            if suffix in ("", "/"):
                return self.send_html(self.render_app_page(role="laptop"))
            if suffix == "/api/state":
                return self.handle_api_state(role="laptop")
            if suffix.startswith("/download/"):
                return self.serve_item_file(suffix.rsplit("/", 1)[1], inline=False)
            if suffix.startswith("/preview/"):
                return self.serve_item_file(suffix.rsplit("/", 1)[1], inline=True)
            return self.deny()

        if self.is_phone(path):
            state.mark_phone_seen()
            suffix = path[len(self.phone_path()) :]
            if suffix in ("", "/"):
                return self.send_html(self.render_app_page(role="phone"))
            if suffix == "/api/state":
                return self.handle_api_state(role="phone")
            if suffix.startswith("/download/"):
                return self.serve_item_file(suffix.rsplit("/", 1)[1], inline=False)
            if suffix.startswith("/preview/"):
                return self.serve_item_file(suffix.rsplit("/", 1)[1], inline=True)
            return self.deny()

        return self.deny()

    def do_POST(self) -> None:
        state = self.server.state
        if state.expired():
            return self.deny(HTTPStatus.GONE, "This QuickShare session has expired.")
        path = urllib.parse.urlsplit(self.path).path
        while "//" in path:
            path = path.replace("//", "/")
        path = self._normalize_alias_path(path)

        if self.is_admin(path):
            return self.handle_admin_post(path[len(self.admin_path()) :])

        if self.is_phone(path):
            state.mark_phone_seen()
            suffix = path[len(self.phone_path()) :]
            if suffix in ("/upload", "/files", "/images", "/folder", "/text"):
                return self.handle_upload_post(sender="phone", suffix=suffix)
            if suffix.startswith("/delete/"):
                item_id = suffix.rsplit("/", 1)[1]
                state.delete_item(item_id)
                return self.send_json({"ok": True})

        return self.deny()

    def parse_form(self) -> cgi.FieldStorage | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.deny(HTTPStatus.BAD_REQUEST, "Invalid request length.")
            return None
        if length <= 0 or length > self.server.state.max_upload:
            self.deny(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"Upload exceeds the {self.server.state.max_upload // MIB} MiB limit.",
            )
            return None
        ctype = self.headers.get_content_type()
        if ctype not in ("multipart/form-data", "application/x-www-form-urlencoded"):
            self.deny(HTTPStatus.BAD_REQUEST, "Expected a form upload.")
            return None
        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(length),
        }
        try:
            return cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ=environ,
                keep_blank_values=True,
            )
        except Exception:
            self.deny(HTTPStatus.BAD_REQUEST, "Could not read upload data.")
            return None

    def handle_api_state(self, role: str) -> None:
        state = self.server.state
        base = self.admin_path() if role == "laptop" else self.phone_path()
        with state.lock:
            items_data = [item.to_dict(base) for item in reversed(state.items)]
            payload = {
                "ok": True,
                "role": role,
                "screen": state.screen,
                "initial_focus": state.initial_focus,
                "phone_connected": state.phone_connected,
                "phone_url": self.phone_url(),
                "phone_ip_url": self.phone_ip_url(),
                "laptop_url": self.laptop_url(),
                "remaining_seconds": state.remaining_seconds(),
                "items": items_data,
            }
        self.send_json(payload)

    def handle_admin_post(self, suffix: str) -> None:
        state = self.server.state
        if suffix in ("/receive", "/open-send", "/start"):
            mode = "receive" if suffix == "/receive" else "send"
            state.start_phone_session(mode)
            if self.wants_json():
                return self.send_json(
                    {
                        "ok": True,
                        "screen": state.screen,
                        "initial_focus": state.initial_focus,
                    }
                )
            return self.redirect(self.admin_path())

        if suffix == "/home":
            with state.lock:
                state.screen = "home"
            if self.wants_json():
                return self.send_json({"ok": True, "screen": "home"})
            return self.redirect(self.admin_path())

        if suffix == "/clipboard":
            try:
                item = state.add_clipboard()
            except ValueError as error:
                if self.wants_json():
                    return self.send_json(
                        {"ok": False, "error": str(error)}, status=HTTPStatus.BAD_REQUEST
                    )
                return self.send_html(
                    self.render_app_page(role="laptop", error=str(error)),
                    HTTPStatus.BAD_REQUEST,
                )
            if self.wants_json():
                return self.send_json(
                    {"ok": True, "added": [item.to_dict(self.admin_path())]}
                )
            return self.redirect(self.admin_path())

        if suffix in ("/upload", "/images", "/files", "/folder", "/text"):
            return self.handle_upload_post(sender="laptop", suffix=suffix)

        if suffix == "/send":
            state.start_phone_session("send")
            if self.wants_json():
                return self.send_json({"ok": True, "screen": "workspace"})
            return self.redirect(self.admin_path())

        if suffix.startswith("/delete/"):
            item_id = suffix.rsplit("/", 1)[1]
            state.delete_item(item_id)
            return self.send_json({"ok": True})

        if suffix == "/stop":
            if self.wants_json():
                self.send_json({"ok": True, "stopped": True})
            else:
                self.send_html(
                    "<!doctype html><html><body style='font-family:system-ui;padding:40px;text-align:center'><h2>QuickShare session stopped</h2><p>You can safely close this tab.</p></body></html>"
                )
            threading.Thread(target=self.stop_later, daemon=True).start()
            return

        return self.deny()

    def handle_upload_post(self, sender: str, suffix: str) -> None:
        form = self.parse_form()
        if form is None:
            return
        state = self.server.state
        base = self.admin_path() if sender == "laptop" else self.phone_path()
        added: list[TransferItem] = []

        try:
            # 1. Check if text message was submitted
            text_val = form.getfirst("message", "")
            if text_val and text_val.strip():
                label = "laptop-note.txt" if sender == "laptop" else "phone-note.txt"
                added.append(state.add_text_item(sender, text_val, label))

            # 2. Check if folder upload was submitted
            is_folder = suffix == "/folder" or form.getfirst("is_folder", "") == "1"
            if "folderitems" in form:
                added.extend(
                    state.save_uploaded_items(
                        sender, form, "folderitems", folder=True
                    )
                )
            elif "items" in form:
                added.extend(
                    state.save_uploaded_items(
                        sender, form, "items", folder=is_folder
                    )
                )
            elif "files" in form:
                added.extend(
                    state.save_uploaded_items(
                        sender, form, "files", folder=is_folder
                    )
                )

            if not added:
                raise ValueError("Please select a file, folder, image, or enter text first.")

        except ValueError as error:
            if self.wants_json():
                return self.send_json(
                    {"ok": False, "error": str(error)}, status=HTTPStatus.BAD_REQUEST
                )
            return self.send_html(
                self.render_app_page(role=sender, error=str(error)),
                HTTPStatus.BAD_REQUEST,
            )

        if self.wants_json():
            return self.send_json(
                {
                    "ok": True,
                    "added": [it.to_dict(base) for it in added],
                }
            )
        return self.redirect(base)

    def stop_later(self) -> None:
        time.sleep(0.25)
        self.server.shutdown()

    def serve_item_file(self, item_id: str, *, inline: bool = False) -> None:
        item = self.server.state.get_item(item_id)
        if item is None or not item.path.exists():
            return self.deny(HTTPStatus.NOT_FOUND, "Item no longer available.")
        try:
            size = item.path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", item.mime)
            disposition = "inline" if inline else "attachment"
            encoded_name = urllib.parse.quote(item.name)
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{encoded_name}",
            )
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with item.path.open("rb") as source:
                shutil.copyfileobj(source, self.wfile)
        except (OSError, BrokenPipeError):
            pass

    def qr_data_uri(self) -> str:
        vendor = Path(__file__).with_name(".quickshare_vendor")
        if vendor.is_dir() and str(vendor) not in sys.path:
            sys.path.append(str(vendor))
        try:
            import qrcode

            image = qrcode.make(self.phone_url())
            output = BytesIO()
            image.save(output, format="PNG")
            return "data:image/png;base64," + base64.b64encode(
                output.getvalue()
            ).decode("ascii")
        except Exception:
            try:
                import qrcode
                import qrcode.image.svg

                image = qrcode.make(
                    self.phone_url(),
                    image_factory=qrcode.image.svg.SvgPathImage,
                )
                output = BytesIO()
                image.save(output)
                return "data:image/svg+xml;base64," + base64.b64encode(
                    output.getvalue()
                ).decode("ascii")
            except Exception:
                return ""

    def phone_url(self) -> str:
        return f"http://{self.server.state.mdns_host}:{self.server.server_port}{self.phone_path()}"

    def phone_ip_url(self) -> str:
        return f"http://{self.server.state.bind_ip}:{self.server.server_port}{self.phone_path()}"

    def laptop_url(self) -> str:
        return f"http://localhost:{self.server.server_port}{self.admin_path()}"

    def serve_static_asset(self, filename: str) -> None:
        frontend_dir = Path(__file__).with_name("frontend")
        target = (frontend_dir / filename).resolve()
        if not target.is_file() or target.parent != frontend_dir.resolve():
            return self.deny(HTTPStatus.NOT_FOUND, "Static file not found.")
        ctype = "text/plain; charset=utf-8"
        if filename.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif filename.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif filename.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        self.send_text(target.read_text(encoding="utf-8"), HTTPStatus.OK, ctype)

    def serve_static_asset(self, filename: str) -> None:
        frontend_dir = Path(__file__).with_name("frontend")
        target = (frontend_dir / filename).resolve()
        if not target.is_file() or target.parent != frontend_dir.resolve():
            return self.deny(HTTPStatus.NOT_FOUND, "Static file not found.")
        ctype = "text/plain; charset=utf-8"
        if filename.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif filename.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif filename.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        self.send_text(target.read_text(encoding="utf-8"), HTTPStatus.OK, ctype)

    def render_app_page(self, role: str = "laptop", error: str = "") -> str:
        state = self.server.state
        base_path = self.admin_path() if role == "laptop" else self.phone_path()
        qr_uri = self.qr_data_uri() if role == "laptop" else ""
        phone_link = self.phone_url()
        initial_state = {
            "role": role,
            "basePath": base_path,
            "screen": state.screen if role == "laptop" else "workspace",
            "initialFocus": state.initial_focus,
            "phoneConnected": state.phone_connected,
            "phoneUrl": phone_link,
            "phoneIpUrl": self.phone_ip_url(),
            "laptopUrl": self.laptop_url(),
            "qrDataUri": qr_uri,
            "maxUploadMib": state.max_upload // MIB,
            "remainingSeconds": state.remaining_seconds(),
            "error": error,
            "items": [
                it.to_dict(base_path) for it in reversed(state.items)
            ],
        }
        state_json = json.dumps(initial_state, ensure_ascii=False).replace("</", "<\\/")
        title = "QuickShare • Laptop Hub" if role == "laptop" else "QuickShare • Mobile Hub"
        template_path = Path(__file__).with_name("frontend") / "index.html"
        template_html = template_path.read_text(encoding="utf-8")
        return template_html.replace("__PAGE_TITLE__", html.escape(title)).replace(
            "__INITIAL_STATE_JSON__", state_json
        )

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start the temporary two-way QuickShare web app."
    )
    parser.add_argument("--bind", help="Private LAN IPv4 address to bind")
    parser.add_argument(
        "--port", type=int, default=8765, help="Port (default: 8765 for a consistent URL; use 0 for random)"
    )
    parser.add_argument(
        "--random-token",
        action="store_true",
        help="Generate random one-time URL tokens on each launch",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=60,
        help="Session timeout in minutes; 0 means manual stop",
    )
    parser.add_argument(
        "--max-upload-mib",
        type=int,
        default=500,
        help="Maximum upload request size in MiB",
    )
    parser.add_argument(
        "--inbox",
        default="~/QuickShare-Inbox",
        help="Optional directory when --save-inbox is used",
    )
    parser.add_argument(
        "--save-inbox",
        action="store_true",
        help="Also save copies of incoming phone uploads to --inbox directory",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the laptop browser",
    )
    args = parser.parse_args()
    if args.minutes < 0 or args.max_upload_mib < 1 or not 0 <= args.port <= 65535:
        parser.error("Invalid limit or port")
    addresses = private_ipv4_addresses()
    bind_ip = args.bind or (addresses[0] if addresses else None)
    if not bind_ip:
        parser.error(
            "Could not select a private LAN address. Use --bind <your Wi-Fi IPv4>. Found: "
            + ", ".join(addresses or ["none"])
        )
    try:
        if not ipaddress.ip_address(bind_ip).is_private:
            parser.error("--bind must be a private IPv4 address")
    except ValueError:
        parser.error("--bind must be an IPv4 address")

    with tempfile.TemporaryDirectory(prefix="quickshare-") as work:
        mdns = MdnsPublisher(bind_ip, alias="quickshare.local")
        args.mdns_host = mdns.start()
        state = ShareState(args, bind_ip, Path(work))
        listen_host = args.bind if args.bind else "0.0.0.0"
        try:
            server = QuickShareServer((listen_host, args.port), state)
        except OSError:
            server = QuickShareServer((listen_host, 0), state)
        admin_route = f"/laptop/{state.admin_token}" if state.admin_token else "/laptop"
        phone_route = f"/phone/{state.phone_token}" if state.phone_token else "/phone"
        laptop_url = f"http://localhost:{server.server_port}{admin_route}"
        laptop_mdns_url = f"http://{state.mdns_host}:{server.server_port}{admin_route}"
        phone_url = f"http://{state.mdns_host}:{server.server_port}{phone_route}"
        phone_ip_url = f"http://{bind_ip}:{server.server_port}{phone_route}"
        print(
            "QuickShare is running (two-way session, no background service)."
            f"\n  Laptop URL (Wi-Fi independent): {laptop_url}  (or {laptop_mdns_url})"
            f"\n  Phone URL  (Wi-Fi independent): {phone_url}"
            f"\n  Phone Fallback IP URL:          {phone_ip_url}"
            "\nPress Ctrl+C to stop.",
            flush=True,
        )
        timer = (
            threading.Timer(args.minutes * 60, server.shutdown)
            if args.minutes
            else None
        )
        if timer:
            timer.daemon = True
            timer.start()
        try:
            if not args.no_browser:
                webbrowser.open(laptop_url)
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            print("\nStopping QuickShare…")
        finally:
            mdns.stop()
            server.server_close()
            if timer:
                timer.cancel()
    print("QuickShare stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
