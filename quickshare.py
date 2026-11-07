#!/usr/bin/env python3
"""QuickShare LAN — A two-way, token-protected laptop-to-phone sharing app."""
from __future__ import annotations

import argparse
import base64
import cgi
import ctypes
import errno
import hashlib
import hmac
import html
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import ssl
import stat
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
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

MIB = 1024 * 1024
MAX_CLIPBOARD_CHARS = 2000

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico", ".jfif", ".tif", ".tiff"
}
ZIP_EXTENSIONS = {".zip"}
VIDEO_EXTENSIONS = {".mp4", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus"}
DOCUMENT_PREVIEW_EXTENSIONS = {".pdf", ".md", ".json", ".py"}
ALREADY_COMPRESSED_EXTENSIONS = {
    ".7z", ".avif", ".br", ".bz2", ".docx", ".gif", ".gz", ".heic", ".jpeg",
    ".jpg", ".jxl", ".m4a", ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".mpeg",
    ".mpg", ".odp", ".ods", ".odt", ".ogg", ".opus", ".pdf", ".png", ".pptx",
    ".rar", ".webm", ".webp", ".woff", ".woff2", ".xlsx", ".xz", ".zip", ".zst",
}
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"COM{digit}" for digit in "¹²³"),
    *(f"LPT{digit}" for digit in "¹²³"),
}


def zip_compression_for_name(filename: str) -> int:
    if Path(filename).suffix.lower() in ALREADY_COMPRESSED_EXTENSIONS:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


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


def default_trusted_devices_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "QuickShare" / "trusted_devices.json"
    return Path.home() / ".config" / "quickshare" / "trusted_devices.json"


def windows_remote_key_code(action: str) -> int | None:
    return {
        "media-toggle": 0xB3,  # VK_MEDIA_PLAY_PAUSE
        "volume-down": 0xAE,  # VK_VOLUME_DOWN
        "volume-up": 0xAF,  # VK_VOLUME_UP
        "slide-previous": 0x25,  # VK_LEFT
        "slide-next": 0x27,  # VK_RIGHT
    }.get(action)


def send_windows_remote_key(action: str) -> bool:
    key_code = windows_remote_key_code(action)
    if key_code is None:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event(key_code, 0, 0, 0)
    user32.keybd_event(key_code, 0, 0x0002, 0)
    return True


def windows_clipboard_text() -> str | None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_int
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_int
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_int

    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return None
        data = kernel32.GlobalLock(handle)
        if not data:
            return None
        try:
            return ctypes.wstring_at(data)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def capture_laptop_screenshot(destination: Path, command: list[str] | None) -> None:
    if os.name == "nt":
        from PIL import ImageGrab

        ImageGrab.grab(all_screens=True).save(destination, format="PNG")
        return
    if command is None:
        raise RuntimeError("No Linux screenshot utility is available.")
    subprocess.run(
        [*command, str(destination)],
        check=True,
        capture_output=True,
        timeout=15,
    )


class MdnsPublisher:
    """Publishes quickshare.local with Avahi or the cross-platform Zeroconf library."""

    def __init__(self, bind_ip: str, alias: str = "quickshare.local") -> None:
        self.bind_ip = bind_ip
        self.mdns_host = get_system_mdns_hostname()
        self.alias = alias
        self.proc: subprocess.Popen | None = None
        self.active_hostname: str = get_system_mdns_hostname()
        self.zeroconf = None
        self.service_info = None
        self.warning = ""

    def start(self, port: int | None = None) -> str:
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

        if port is not None:
            try:
                    from zeroconf import Error as ZeroconfError
                    from zeroconf import ServiceInfo, Zeroconf
            except ImportError as error:
                    self.warning = f"Could not load mDNS support: {error}"
                    return self.active_hostname

            try:
                    self.zeroconf = Zeroconf()
                    self.service_info = ServiceInfo(
                        "_http._tcp.local.",
                        "QuickShare._http._tcp.local.",
                        addresses=[socket.inet_aton(self.bind_ip)],
                        port=port,
                        server=f"{self.alias}.",
                    )
                    self.zeroconf.register_service(self.service_info)
                    self.active_hostname = self.alias
            except (OSError, ZeroconfError) as error:
                    if self.zeroconf is not None:
                        self.zeroconf.close()
                        self.zeroconf = None
                    self.service_info = None
                    self.warning = f"Could not publish {self.alias}: {error}"
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
        if self.zeroconf is not None:
            if self.service_info is not None:
                self.zeroconf.unregister_service(self.service_info)
            self.zeroconf.close()
            self.zeroconf = None
            self.service_info = None


def safe_name(name: str, fallback: str = "upload") -> str:
    name = Path(name).name.replace("\x00", "").strip()
    cleaned = "".join(
        char if char.isalnum() or char in " ._-()[]" else "_" for char in name
    ).strip(" .")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:180]


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
    if os.name == "nt":
        return windows_clipboard_text()

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


def classify_file_kind(filename: str, mime: str = "") -> str:
    ext = Path(filename).suffix.lower()
    if ext in ZIP_EXTENSIONS:
        return "zip"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if mime.startswith("audio/") or ext in AUDIO_EXTENSIONS:
        return "audio"
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
    burn_after_read: bool = False

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
            "burn_after_read": self.burn_after_read,
            "download_url": f"{base_url}/download/{self.id}",
            "preview_url": (
                f"{base_url}/preview/{self.id}"
                if self.kind == "image"
                or self.mime.startswith(("audio/", "video/"))
                or Path(self.name).suffix.lower() in (
                    DOCUMENT_PREVIEW_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
                )
                else ""
            ),
        }


class ShareState:
    def __init__(self, args: argparse.Namespace, bind_ip: str, work: Path):
        use_random = getattr(args, "random_token", False)
        self.admin_token = secrets.token_urlsafe(16) if use_random else ""
        # Fixed URL path by default so the phone URL stays identical across launches
        self.phone_token: str = secrets.token_urlsafe(16) if use_random else "" 
        self.bind_ip = bind_ip
        self.mdns_host = getattr(args, "mdns_host", "") or get_system_mdns_hostname()
        self.scheme = getattr(args, "scheme", "http")
        self.pin: str | None = getattr(args, "pin", None)
        self.pin_cookie = secrets.token_urlsafe(32)
        self.pin_attempts: dict[str, tuple[int, float]] = {}
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
        self.scratchpad = ""
        self.scratchpad_revision = 0
        self._counter = 0
        self.screen = "home"  # "home" or "workspace"
        self.initial_focus = "send"  # "send" or "receive"
        self.phone_connected = False
        self.last_phone_seen: float = 0.0
        self.phone_devices: dict[str, dict[str, object]] = {}
        self.lock = threading.Lock()
        self.trusted_devices_path = Path(
            getattr(
                args,
                "trusted_devices_file",
                default_trusted_devices_path(),
            )
        ).expanduser()
        self.trusted_devices = self._load_trusted_devices()

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

    def mark_phone_request(self) -> None:
        with self.lock:
            if self.screen == "home":
                self.screen = "workspace"

    def request_phone_approval(
        self, token: str | None, client_ip: str, user_agent: str, *, retry: bool = False
    ) -> tuple[str, bool]:
        with self.lock:
            valid_token = bool(token and re.fullmatch(r"[A-Za-z0-9_-]{32}", token))
            if not valid_token:
                token = secrets.token_urlsafe(24)
            if token not in self.phone_devices:
                trusted = self.trusted_devices.get(self._device_fingerprint(token))
                self.phone_devices[token] = {
                    "ip": client_ip,
                    "user_agent": user_agent[:200],
                    "requested_at": time.time(),
                    "approved": trusted is not None,
                    "denied": False,
                }
                return token, not valid_token
            device = self.phone_devices[token]
            if retry and device["denied"]:
                device["denied"] = False
                device["requested_at"] = time.time()
            return token, False

    def phone_approval_status(self, token: str | None) -> dict[str, bool]:
        with self.lock:
            device = self.phone_devices.get(token or "")
            return {
                "known": device is not None,
                "approved": bool(device and device["approved"]),
                "denied": bool(device and device["denied"]),
            }

    def pending_phone_devices(self) -> list[dict[str, str]]:
        with self.lock:
            return [
                {
                    "id": token,
                    "ip": str(device["ip"]),
                    "name": self._phone_device_name(str(device["user_agent"])),
                }
                for token, device in self.phone_devices.items()
                if not device["approved"] and not device["denied"]
            ]

    @staticmethod
    def _phone_device_name(user_agent: str) -> str:
        agent = user_agent.lower()
        if "android" in agent:
            return "Android phone"
        if "iphone" in agent:
            return "iPhone"
        if "ipad" in agent:
            return "iPad"
        return "Phone or mobile browser"

    @staticmethod
    def _device_fingerprint(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _load_trusted_devices(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self.trusted_devices_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Could not load remembered QuickShare devices from {self.trusted_devices_path}: {error}"
            ) from error
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("devices"), dict):
            raise RuntimeError(f"Invalid remembered-device data in {self.trusted_devices_path}.")
        return {
            fingerprint: {
                "name": str(record.get("name", "Phone or mobile browser")),
                "approved_at": str(record.get("approved_at", "")),
            }
            for fingerprint, record in data["devices"].items()
            if isinstance(fingerprint, str)
            and len(fingerprint) == 64
            and isinstance(record, dict)
        }

    def _save_trusted_devices(self) -> None:
        path = self.trusted_devices_path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(path.parent, 0o700)
        payload = json.dumps(
            {"version": 1, "devices": self.trusted_devices},
            ensure_ascii=False,
            indent=2,
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            if os.name == "posix":
                os.chmod(temporary_path, 0o600)
            temporary_path.replace(path)
        except OSError:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def trusted_phone_devices(self) -> list[dict[str, str]]:
        with self.lock:
            return [
                {
                    "id": fingerprint,
                    "name": record["name"],
                    "approved_at": record["approved_at"],
                }
                for fingerprint, record in self.trusted_devices.items()
            ]

    def set_phone_approval(self, token: str, approved: bool, *, remember: bool = False) -> bool:
        with self.lock:
            device = self.phone_devices.get(token)
            if device is None or device["approved"] or device["denied"]:
                return False
            if approved and remember:
                fingerprint = self._device_fingerprint(token)
                previous = self.trusted_devices.get(fingerprint)
                self.trusted_devices[fingerprint] = {
                    "name": self._phone_device_name(str(device["user_agent"])),
                    "approved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
                try:
                    self._save_trusted_devices()
                except OSError:
                    if previous is None:
                        self.trusted_devices.pop(fingerprint, None)
                    else:
                        self.trusted_devices[fingerprint] = previous
                    raise
            device["approved"] = approved
            device["denied"] = not approved
            return True

    def forget_phone_device(self, fingerprint: str) -> bool:
        with self.lock:
            if fingerprint not in self.trusted_devices:
                return False
            previous = self.trusted_devices[fingerprint]
            del self.trusted_devices[fingerprint]
            try:
                self._save_trusted_devices()
            except OSError:
                self.trusted_devices[fingerprint] = previous
                raise
            for token, device in self.phone_devices.items():
                if self._device_fingerprint(token) == fingerprint:
                    device["approved"] = False
                    device["denied"] = True
            return True

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

    def add_text_item(
        self,
        sender: str,
        text: str,
        label: str = "text-snippet.txt",
        *,
        burn_after_read: bool = False,
    ) -> TransferItem:
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
                burn_after_read=burn_after_read,
            )
            self.items.append(item)
            if sender == "laptop":
                self.downloads.append(target)
                self.selection_label = "Text snippet"
                self.screen = "workspace"
            else:
                self.received.append(f"Text: {cleaned[:200]}")
            return item

    def consume_burn_item(self, item_id: str, consumer_role: str) -> bool:
        with self.lock:
            for index, item in enumerate(self.items):
                if (
                    item.id != item_id
                    or not item.burn_after_read
                    or item.sender == consumer_role
                ):
                    continue
                item.path.unlink(missing_ok=True)
                self.items.pop(index)
                return True
        return False

    def claim_item_download(
        self, item_id: str, consumer_role: str, *, inline: bool
    ) -> tuple[TransferItem, BinaryIO] | None:
        with self.lock:
            for index, item in enumerate(self.items):
                if item.id != item_id or not item.path.is_file():
                    continue
                source = item.path.open("rb")
                if not inline and item.burn_after_read and item.sender != consumer_role:
                    try:
                        item.path.unlink()
                    except OSError:
                        source.close()
                        raise
                    self.items.pop(index)
                return item, source
        return None

    def add_clipboard(self) -> TransferItem:
        text = clipboard_text()
        if text is None or not text.strip():
            hint = (
                "Check that the Windows clipboard is available."
                if os.name == "nt"
                else "Install wl-clipboard, xclip, or xsel."
            )
            raise ValueError(
                f"System clipboard is empty or unavailable. You can paste directly into the Text box instead. {hint}"
            )
        return self.add_text_item("laptop", text, "clipboard.txt")

    def update_scratchpad(self, content: str) -> int:
        with self.lock:
            if content != self.scratchpad:
                self.scratchpad = content
                self.scratchpad_revision += 1
            return self.scratchpad_revision

    def add_local_path(self, source: Path) -> list[TransferItem]:
        if not source.exists():
            raise ValueError(f"Path does not exist: {source}")
        if source.is_symlink():
            raise ValueError(f"Symbolic links are not supported: {source}")

        if source.is_dir():
            files = sorted(path for path in source.rglob("*") if path.is_file() and not path.is_symlink())
            if not files:
                raise ValueError(f"Folder is empty: {source}")
            archive_path = unique_path(self.storage_dir, f"{source.name or 'folder'}.zip")
            with zipfile.ZipFile(
                archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1
            ) as archive:
                for path in files:
                    archive.write(
                        path,
                        path.relative_to(source),
                        compress_type=zip_compression_for_name(path.name),
                    )
            return [self._register_local_file(archive_path, is_folder_zip=True)]

        if not source.is_file():
            raise ValueError(f"Not a regular file: {source}")
        target = unique_path(self.storage_dir, source.name)
        shutil.copy2(source, target)
        return [self._register_local_file(target)]

    def _register_local_file(self, path: Path, *, is_folder_zip: bool = False) -> TransferItem:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        item = TransferItem(
            id=self._next_id(),
            sender="laptop",
            kind="zip" if is_folder_zip else classify_file_kind(path.name),
            name=path.name,
            path=path,
            size=path.stat().st_size,
            mime=mime,
            is_folder_zip=is_folder_zip,
        )
        with self.lock:
            self.items.append(item)
            self.downloads.append(path)
            self.selection_label = item.name
            self.screen = "workspace"
        return item

    def save_to_inbox(self, item_id: str) -> Path:
        with self.lock:
            item = next((candidate for candidate in self.items if candidate.id == item_id), None)
            if item is None or not item.path.is_file():
                raise FileNotFoundError("Item no longer available.")
            inbox = Path.home() / "QuickShare-Inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            destination = unique_path(inbox, item.name)
            shutil.copy2(item.path, destination)
            return destination

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
            relative_paths = [
                safe_relative_path(item.filename) for item in valid_items
            ]
            files_by_path = {
                rel_path.as_posix(): (item, rel_path)
                for item, rel_path in zip(valid_items, relative_paths)
            }
            top_folder_name = next(
                (path.parts[0] for path in relative_paths if len(path.parts) > 1),
                "folder",
            )
            with self.lock:
                archive_path = unique_path(
                    self.storage_dir, f"{safe_name(top_folder_name, 'folder')}.zip"
                )
                archive_path.touch(exist_ok=False)

            try:
                with zipfile.ZipFile(
                    archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1
                ) as zf:
                    for item, rel_path in files_by_path.values():
                        info = zipfile.ZipInfo(rel_path.as_posix())
                        info.compress_type = zip_compression_for_name(rel_path.name)
                        with zf.open(info, "w", force_zip64=True) as dest:
                            while chunk := item.file.read(MIB):
                                dest.write(chunk)
            except Exception:
                archive_path.unlink(missing_ok=True)
                raise

            with self.lock:
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

                guessed_mime = mimetypes.guess_type(target.name)[0]
                upload_mime = getattr(item, "type", "")
                mime = (
                    upload_mime
                    if upload_mime.startswith("audio/")
                    and Path(target.name).suffix.lower()
                    in AUDIO_EXTENSIONS | {".webm"}
                    else guessed_mime or "application/octet-stream"
                )
                kind = classify_file_kind(target.name, mime)
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

    def phone_is_unlocked(self) -> bool:
        state = self.server.state
        if not state.pin:
            return True
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        cookie = cookies.get("quickshare_pin")
        return bool(
            cookie
            and hmac.compare_digest(
                cookie.value.encode("utf-8"), state.pin_cookie.encode("utf-8")
            )
        )

    def phone_device_token(self) -> str | None:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = cookie.get("quickshare_device")
        return morsel.value if morsel else None

    def phone_is_approved(self) -> bool:
        status = self.server.state.phone_approval_status(self.phone_device_token())
        return status["approved"]

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

        if path == "/service-worker.js":
            return self.serve_service_worker()

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
            if suffix == "/download-all":
                return self.serve_received_zip("laptop")
            if suffix == "/zip":
                return self.handle_zip_contents()
            if suffix == "/zip/file":
                return self.serve_zip_member()
            if suffix == "/storage":
                return self.send_json(self.storage_status())
            if suffix.startswith("/download/"):
                return self.serve_item_file(suffix.rsplit("/", 1)[1], role="laptop")
            if suffix.startswith("/preview/"):
                return self.serve_item_file(suffix.rsplit("/", 1)[1], role="laptop", inline=True)
            return self.deny()

        if self.is_phone(path):
            suffix = path[len(self.phone_path()) :]
            if suffix == "/manifest.json":
                return self.serve_manifest()
            if not self.phone_is_unlocked():
                if suffix in ("", "/"):
                    return self.send_html(self.render_pin_page())
                return self.deny(HTTPStatus.UNAUTHORIZED, "Enter the session PIN to unlock this phone.")
            if suffix in ("", "/"):
                return self.handle_phone_landing()
            if suffix == "/approval-status":
                status = self.server.state.phone_approval_status(
                    self.phone_device_token()
                )
                return self.send_json({"ok": True, **status})
            if not self.phone_is_approved():
                return self.deny(
                    HTTPStatus.FORBIDDEN,
                    "This phone must be approved on the laptop before accessing the session.",
                )
            state.mark_phone_seen()
            if suffix == "/api/state":
                return self.handle_api_state(role="phone")
            if suffix == "/download-all":
                return self.serve_received_zip("phone")
            if suffix == "/storage":
                return self.send_json(self.storage_status())
            if suffix == "/remote/screenshot":
                return self.serve_laptop_screenshot()
            if suffix == "/zip":
                return self.handle_zip_contents()
            if suffix == "/zip/file":
                return self.serve_zip_member()
            if suffix.startswith("/download/"):
                return self.serve_item_file(suffix.rsplit("/", 1)[1], role="phone")
            if suffix.startswith("/preview/"):
                return self.serve_item_file(suffix.rsplit("/", 1)[1], role="phone", inline=True)
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
            suffix = path[len(self.phone_path()) :]
            if suffix == "/unlock" and state.pin:
                return self.handle_pin_unlock()
            if not self.phone_is_unlocked():
                return self.deny(HTTPStatus.UNAUTHORIZED, "Enter the session PIN to unlock this phone.")
            if suffix == "/request-approval":
                token, _ = state.request_phone_approval(
                    self.phone_device_token(),
                    self.client_address[0],
                    self.headers.get("User-Agent", ""),
                    retry=True,
                )
                state.mark_phone_request()
                self.send_response(HTTPStatus.OK)
                self.send_header("Set-Cookie", self.phone_device_cookie(token))
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if not self.phone_is_approved():
                return self.deny(
                    HTTPStatus.FORBIDDEN,
                    "This phone must be approved on the laptop before accessing the session.",
                )
            state.mark_phone_seen()
            if suffix in ("/upload", "/files", "/images", "/folder", "/text"):
                return self.handle_upload_post(sender="phone", suffix=suffix)
            if suffix == "/scratchpad":
                return self.handle_scratchpad_post()
            if suffix == "/share-target":
                return self.handle_share_target()
            if suffix == "/open-url":
                return self.handle_open_url()
            if suffix.startswith("/remote/"):
                return self.handle_remote_action(suffix[len("/remote/"):])
            if suffix.startswith("/consume/"):
                item_id = suffix.rsplit("/", 1)[1]
                try:
                    if state.consume_burn_item(item_id, "phone"):
                        return self.send_json({"ok": True, "consumed": True})
                except OSError as error:
                    return self.send_json(
                        {"ok": False, "error": f"Could not remove this one-time item: {error}"},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                return self.deny(HTTPStatus.CONFLICT, "This one-time item was already used or is unavailable.")
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
        free_bytes = shutil.disk_usage(self.server.state.work).free
        storage_multiplier = 3 if self.server.state.save_inbox else 2
        required_bytes = length * storage_multiplier
        if required_bytes > free_bytes:
            self.deny(
                HTTPStatus.INSUFFICIENT_STORAGE,
                f"Upload may need up to {format_size(required_bytes)} of temporary storage, but only {format_size(free_bytes)} is available.",
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
        disk = self.storage_status()
        pending_devices = state.pending_phone_devices() if role == "laptop" else []
        trusted_devices = state.trusted_phone_devices() if role == "laptop" else []
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
                "scratchpad": state.scratchpad,
                "scratchpad_revision": state.scratchpad_revision,
                "pending_devices": pending_devices,
                "trusted_devices": trusted_devices,
                **disk,
                "remaining_seconds": state.remaining_seconds(),
                "items": items_data,
            }
        self.send_json(payload)

    def phone_device_cookie(self, token: str) -> str:
        secure = "; Secure" if self.server.state.scheme == "https" else ""
        return (
            f"quickshare_device={token}; HttpOnly; SameSite=Strict; "
            f"Path=/{secure}; Max-Age=315360000"
        )

    def handle_phone_landing(self) -> None:
        state = self.server.state
        token, is_new = state.request_phone_approval(
            self.phone_device_token(),
            self.client_address[0],
            self.headers.get("User-Agent", ""),
        )
        status = state.phone_approval_status(token)
        if status["approved"]:
            state.mark_phone_seen()
            return self.send_html(self.render_app_page(role="phone"))
        state.mark_phone_request()
        body = self.render_device_wait_page(status["denied"])
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if is_new:
            self.send_header("Set-Cookie", self.phone_device_cookie(token))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def render_device_wait_page(self, denied: bool) -> str:
        phone_path = json.dumps(self.phone_path())
        denied_message = (
            '<p id="approvalMessage">This connection was not approved.</p>'
            '<button id="requestAgain" type="button" onclick="requestAgain()">Request approval again</button>'
            if denied
            else '<p id="approvalMessage">Approve this phone from the laptop to continue.</p>'
        )
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0b0f19">
<title>QuickShare • Approval</title><style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:#f4f6fb;color:#0f172a;font:16px/1.5 system-ui,sans-serif}}
main{{width:min(440px,100%);padding:26px;background:#fff;border:1px solid #e2e8f0;border-radius:20px;box-shadow:0 12px 32px #0f172a12;text-align:center}}
.mark{{width:48px;height:48px;margin:0 auto 14px;display:grid;place-items:center;border-radius:14px;background:#eef2ff;color:#4f46e5;font-size:24px}}
h1{{margin:0 0 8px;font-size:1.25rem}}p{{margin:0;color:#64748b}}button{{margin-top:16px;padding:11px 16px;border:0;border-radius:10px;background:#4f46e5;color:#fff;font:inherit;font-weight:700}}
</style></head><body><main><div class="mark">✓</div><h1>Waiting for laptop approval</h1>{denied_message}</main>
<script>
const base={phone_path};
async function checkApproval(){{try{{const response=await fetch(base+"/approval-status",{{cache:"no-store"}});const status=await response.json();if(status.approved)location.reload();if(status.denied)document.getElementById("approvalMessage").textContent="This connection was not approved.";}}catch(error){{console.error("Approval check failed",error);}}}}
async function requestAgain(){{const response=await fetch(base+"/request-approval",{{method:"POST",headers:{{"X-Requested-With":"XMLHttpRequest"}}}});if(!response.ok){{document.getElementById("approvalMessage").textContent="Could not request approval. Reload the page to try again.";return;}}location.reload();}}
setInterval(checkApproval,1500);
</script></body></html>"""

    def handle_open_url(self) -> None:
        payload = self.read_json_body(limit=8192)
        if payload is None or not isinstance(payload.get("url"), str):
            return self.deny(HTTPStatus.BAD_REQUEST, "Expected an HTTP or HTTPS URL.")
        raw_url = payload["url"].strip()
        try:
            parsed = urllib.parse.urlsplit(raw_url)
            port = parsed.port
            valid = (
                len(raw_url) <= 4096
                and parsed.scheme.lower() in ("http", "https")
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and (port is None or 1 <= port <= 65535)
                and not any(ord(char) < 32 for char in raw_url)
            )
        except ValueError:
            valid = False
        if not valid:
            return self.deny(HTTPStatus.BAD_REQUEST, "Only valid HTTP or HTTPS links can be opened.")
        try:
            opened = webbrowser.open(raw_url, new=2)
        except (OSError, webbrowser.Error) as error:
            return self.send_json(
                {"ok": False, "error": f"Could not open the laptop browser: {error}"},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if not opened:
            return self.send_json(
                {"ok": False, "error": "The laptop browser could not open this link."},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        self.send_json({"ok": True})

    def read_json_body(self, *, limit: int = MIB) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.deny(HTTPStatus.BAD_REQUEST, "Invalid request length.")
            return None
        if length <= 0 or length > limit:
            self.deny(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body is missing or too large.")
            return None
        if self.headers.get_content_type() != "application/json":
            self.deny(HTTPStatus.BAD_REQUEST, "Expected a JSON request.")
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.deny(HTTPStatus.BAD_REQUEST, "Could not read request data.")
            return None
        if not isinstance(payload, dict):
            self.deny(HTTPStatus.BAD_REQUEST, "Expected a JSON object.")
            return None
        return payload

    def handle_remote_action(self, action: str) -> None:
        if action == "open-url":
            return self.handle_open_url()

        if os.name == "nt":
            try:
                if not send_windows_remote_key(action):
                    return self.deny(
                        HTTPStatus.BAD_REQUEST, "Unknown remote-control action."
                    )
            except OSError as error:
                return self.send_json(
                    {"ok": False, "error": f"Could not send the remote key: {error}"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            self.send_json({"ok": True})
            return

        commands = {
            "media-toggle": ("playerctl", ["play-pause"]),
            "volume-down": ("pactl", ["set-sink-volume", "@DEFAULT_SINK@", "-5%"]),
            "slide-previous": ("xdotool", ["key", "Left"]),
            "slide-next": ("xdotool", ["key", "Right"]),
        }
        if action == "volume-up":
            executable = shutil.which("pactl")
            if not executable:
                return self.send_json(
                    {"ok": False, "error": "pactl is not installed on the laptop."},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            try:
                current = subprocess.run(
                    [executable, "get-sink-volume", "@DEFAULT_SINK@"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                levels = [int(value) for value in re.findall(r"(\d+)%", current.stdout)]
                if not levels:
                    return self.send_json(
                        {"ok": False, "error": "Could not read the current laptop volume."},
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                target = min(100, max(levels) + 5)
                subprocess.run(
                    [executable, "set-sink-volume", "@DEFAULT_SINK@", f"{target}%"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except subprocess.CalledProcessError as error:
                detail = (error.stderr or "").strip()
                return self.send_json(
                    {"ok": False, "error": detail or "pactl could not change laptop volume."},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            except subprocess.TimeoutExpired:
                return self.send_json(
                    {"ok": False, "error": "pactl did not respond in time."},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            self.send_json({"ok": True})
            return
        command = commands.get(action)
        if command is None:
            return self.deny(HTTPStatus.BAD_REQUEST, "Unknown remote-control action.")
        executable = shutil.which(command[0])
        if not executable:
            return self.send_json(
                {
                    "ok": False,
                    "error": f"{command[0]} is not installed on the laptop.",
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        try:
            subprocess.run(
                [executable, *command[1]],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or "").strip()
            return self.send_json(
                {
                    "ok": False,
                    "error": detail or f"{command[0]} could not perform the action.",
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        except subprocess.TimeoutExpired:
            return self.send_json(
                {"ok": False, "error": f"{command[0]} did not respond in time."},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        self.send_json({"ok": True})

    def serve_laptop_screenshot(self) -> None:
        state = self.server.state
        command: list[str] | None = None
        if os.name != "nt":
            for program, args in (
                ("grim", []),
                ("gnome-screenshot", ["-f"]),
                ("scrot", []),
            ):
                executable = shutil.which(program)
                if executable:
                    command = [executable, *args]
                    break
        if os.name != "nt" and command is None:
            return self.send_json(
                {
                    "ok": False,
                    "error": "No screenshot utility found. Install grim (Wayland), gnome-screenshot, or scrot.",
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )

        with tempfile.NamedTemporaryFile(
            prefix="quickshare-screen-", suffix=".png", dir=state.work, delete=False
        ) as temporary:
            screenshot_path = Path(temporary.name)
        try:
            capture_laptop_screenshot(screenshot_path, command)
        except (OSError, subprocess.SubprocessError) as error:
            screenshot_path.unlink(missing_ok=True)
            self.send_json(
                {"ok": False, "error": f"Could not capture the laptop screen: {error}"},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        except (ImportError, RuntimeError) as error:
            screenshot_path.unlink(missing_ok=True)
            self.send_json(
                {"ok": False, "error": f"Could not load the screenshot dependency: {error}"},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        response_started = False
        try:
            size = screenshot_path.stat().st_size
            with screenshot_path.open("rb") as screenshot:
                if screenshot.read(8) != b"\x89PNG\r\n\x1a\n":
                    return self.send_json(
                        {"ok": False, "error": "The screenshot utility did not produce a PNG image."},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                response_started = True
                self.copy_file_to_response(screenshot, 0, size)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as error:
            if not response_started:
                self.send_json(
                    {"ok": False, "error": f"Could not stream the laptop screenshot: {error}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
        finally:
            screenshot_path.unlink(missing_ok=True)

    def handle_zip_contents(self) -> None:
        item_id = urllib.parse.parse_qs(
            urllib.parse.urlsplit(self.path).query
        ).get("id", [""])[0]
        item = self.server.state.get_item(item_id)
        if item is None or item.kind != "zip":
            return self.deny(HTTPStatus.NOT_FOUND, "ZIP archive is no longer available.")
        try:
            with zipfile.ZipFile(item.path) as archive:
                entries = []
                for index, info in enumerate(archive.infolist()):
                    if info.is_dir() or not self.safe_zip_member(info):
                        continue
                    entries.append(
                        {
                            "index": index,
                            "name": info.filename,
                            "size": info.file_size,
                            "size_human": format_size(info.file_size),
                            "downloadable": info.file_size <= self.server.state.max_upload,
                        }
                    )
                    if len(entries) >= 5000:
                        break
        except (OSError, zipfile.BadZipFile, ValueError) as error:
            return self.send_json(
                {"ok": False, "error": f"Could not read ZIP contents: {error}"},
                status=HTTPStatus.BAD_REQUEST,
            )
        self.send_json({"ok": True, "name": item.name, "entries": entries})

    @staticmethod
    def safe_zip_member(info: zipfile.ZipInfo) -> bool:
        normalized = info.filename.replace("\\", "/")
        path = Path(normalized)
        parts = normalized.split("/")
        mode = info.external_attr >> 16
        return bool(
            normalized
            and not normalized.startswith("/")
            and not path.is_absolute()
            and ".." not in parts
            and not (parts and parts[0].endswith(":"))
            and not stat.S_ISLNK(mode)
        )

    def serve_zip_member(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        item_id = query.get("id", [""])[0]
        try:
            entry_index = int(query.get("entry", [""])[0])
        except ValueError:
            return self.deny(HTTPStatus.BAD_REQUEST, "Invalid ZIP entry.")
        item = self.server.state.get_item(item_id)
        if item is None or item.kind != "zip":
            return self.deny(HTTPStatus.NOT_FOUND, "ZIP archive is no longer available.")
        response_started = False
        try:
            with zipfile.ZipFile(item.path) as archive:
                infos = archive.infolist()
                if not 0 <= entry_index < len(infos):
                    return self.deny(HTTPStatus.NOT_FOUND, "ZIP entry not found.")
                info = infos[entry_index]
                if (
                    info.is_dir()
                    or not self.safe_zip_member(info)
                    or info.file_size > self.server.state.max_upload
                ):
                    return self.deny(HTTPStatus.FORBIDDEN, "This ZIP entry cannot be downloaded.")
                entry = archive.open(info, "r")
                self.send_response(HTTPStatus.OK)
                mime = mimetypes.guess_type(info.filename)[0] or "application/octet-stream"
                self.send_header("Content-Type", mime)
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename*=UTF-8''{urllib.parse.quote(safe_name(info.filename))}",
                )
                self.send_header("Content-Length", str(info.file_size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                response_started = True
                with entry:
                    while chunk := entry.read(MIB):
                        self.wfile.write(chunk)
        except (OSError, zipfile.BadZipFile, RuntimeError, EOFError) as error:
            if response_started:
                return
            return self.send_json(
                {"ok": False, "error": f"Could not read ZIP entry: {error}"},
                status=HTTPStatus.BAD_REQUEST,
            )

    def storage_status(self) -> dict:
        usage = shutil.disk_usage(self.server.state.work)
        return {
            "free_disk_bytes": usage.free,
            "free_disk_human": format_size(usage.free),
            "total_disk_bytes": usage.total,
        }

    def handle_admin_post(self, suffix: str) -> None:
        state = self.server.state
        if suffix == "/open-url":
            return self.handle_open_url()

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

        if suffix.startswith("/save-to-inbox/"):
            item_id = suffix.rsplit("/", 1)[1]
            try:
                saved_path = state.save_to_inbox(item_id)
            except (FileNotFoundError, OSError) as error:
                return self.send_json(
                    {"ok": False, "error": str(error)},
                    status=HTTPStatus.NOT_FOUND if isinstance(error, FileNotFoundError) else HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return self.send_json({"ok": True, "path": str(saved_path)})

        if suffix == "/scratchpad":
            return self.handle_scratchpad_post()

        if suffix.startswith("/consume/"):
            item_id = suffix.rsplit("/", 1)[1]
            try:
                if state.consume_burn_item(item_id, "laptop"):
                    return self.send_json({"ok": True, "consumed": True})
            except OSError as error:
                return self.send_json(
                    {"ok": False, "error": f"Could not remove this one-time item: {error}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return self.deny(HTTPStatus.CONFLICT, "This one-time item was already used or is unavailable.")

        if (
            suffix.startswith("/approve-device/")
            or suffix.startswith("/remember-device/")
            or suffix.startswith("/deny-device/")
        ):
            action, separator, device_id = suffix.strip("/").partition("/")
            if not separator or not device_id:
                return self.deny(HTTPStatus.BAD_REQUEST, "Missing device request ID.")
            approved = action != "deny-device"
            remember = action == "remember-device"
            try:
                updated = state.set_phone_approval(device_id, approved, remember=remember)
            except OSError as error:
                return self.send_json(
                    {"ok": False, "error": f"Could not save this trusted device: {error}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            if not updated:
                return self.send_json(
                    {"ok": False, "error": "This device request is no longer pending."},
                    status=HTTPStatus.CONFLICT,
                )
            return self.send_json({"ok": True, "approved": approved})

        if suffix.startswith("/forget-device/"):
            fingerprint = suffix.rsplit("/", 1)[1]
            try:
                forgotten = state.forget_phone_device(fingerprint)
            except OSError as error:
                return self.send_json(
                    {"ok": False, "error": f"Could not forget this device: {error}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            if not forgotten:
                return self.send_json(
                    {"ok": False, "error": "This remembered device was not found."},
                    status=HTTPStatus.NOT_FOUND,
                )
            return self.send_json({"ok": True})

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

    def handle_pin_unlock(self) -> None:
        state = self.server.state
        client = self.client_address[0]
        attempts, blocked_until = state.pin_attempts.get(client, (0, 0.0))
        now = time.monotonic()
        if blocked_until > now:
            return self.deny(HTTPStatus.TOO_MANY_REQUESTS, "Too many incorrect PIN attempts. Try again shortly.")
        if blocked_until:
            attempts = 0

        form = self.parse_form()
        if form is None:
            return
        candidate = form.getfirst("pin", "")
        if not hmac.compare_digest(
            candidate.encode("utf-8"), (state.pin or "").encode("utf-8")
        ):
            attempts += 1
            blocked_until = now + 60 if attempts >= 5 else 0.0
            state.pin_attempts[client] = (attempts, blocked_until)
            if not self.wants_json():
                return self.send_html(self.render_pin_page("Incorrect PIN."), HTTPStatus.FORBIDDEN)
            return self.deny(HTTPStatus.FORBIDDEN, "Incorrect PIN.")

        state.pin_attempts.pop(client, None)
        self.send_response(HTTPStatus.OK if self.wants_json() else HTTPStatus.SEE_OTHER)
        self.send_header(
            "Set-Cookie",
            f"quickshare_pin={state.pin_cookie}; HttpOnly; SameSite=Strict; Path={self.phone_path()}; Max-Age=86400",
        )
        self.send_header("Cache-Control", "no-store")
        if self.wants_json():
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_header("Location", self.phone_path())
            self.send_header("Content-Length", "0")
            self.end_headers()

    def render_pin_page(self, error: str = "") -> str:
        phone_path = html.escape(self.phone_path(), quote=True)
        error_html = f'<p class="pin-error" role="alert">{html.escape(error)}</p>' if error else ""
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0b0f19"><link rel="manifest" href="{phone_path}/manifest.json"><title>QuickShare PIN</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0f19;color:#f8fafc;font:16px system-ui,sans-serif}}main{{width:min(360px,calc(100% - 40px));padding:24px;background:#111827;border:1px solid #334155;border-radius:16px}}label{{display:block;margin:16px 0 6px}}input,button{{width:100%;box-sizing:border-box;padding:12px;border-radius:8px;font:inherit}}input{{border:1px solid #475569;background:#0f172a;color:#fff;letter-spacing:.3em}}button{{margin-top:12px;border:0;background:#6366f1;color:white;font-weight:700}}.pin-error{{color:#f87171}}</style></head>
<body><main><h1>QuickShare is locked</h1><p>Enter the 4-digit session PIN to continue.</p>{error_html}
<form method="post" action="{phone_path}/unlock"><label for="pin">Session PIN</label><input id="pin" name="pin" type="password" inputmode="numeric" pattern="[0-9]{{4}}" maxlength="4" autocomplete="one-time-code" required autofocus><button type="submit">Unlock</button></form></main>
<script>if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/service-worker.js").catch(error => console.error("QuickShare service worker registration failed:", error));</script>
</body></html>"""

    def handle_scratchpad_post(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.deny(HTTPStatus.BAD_REQUEST, "Invalid request length.")
        if length <= 0 or length > MIB:
            return self.deny(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Scratchpad update is too large.")
        if self.headers.get_content_type() != "application/json":
            return self.deny(HTTPStatus.BAD_REQUEST, "Expected a JSON scratchpad update.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.deny(HTTPStatus.BAD_REQUEST, "Could not read scratchpad update.")
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
            return self.deny(HTTPStatus.BAD_REQUEST, "Scratchpad content must be text.")
        revision = self.server.state.update_scratchpad(payload["content"])
        self.send_json({"ok": True, "revision": revision})

    def handle_share_target(self) -> None:
        form = self.parse_form()
        if form is None:
            return
        state = self.server.state
        added: list[TransferItem] = []
        text_parts = [
            value.strip()
            for value in (form.getfirst("title", ""), form.getfirst("text", ""), form.getfirst("url", ""))
            if value and value.strip()
        ]
        if text_parts:
            added.append(state.add_text_item("phone", "\n".join(text_parts), "android-share.txt"))
        if "files" in form:
            added.extend(state.save_uploaded_items("phone", form, "files"))
        if not added:
            return self.send_html("No files or text were shared.", HTTPStatus.BAD_REQUEST)
        return self.redirect(self.phone_path())

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
                added.append(
                    state.add_text_item(
                        sender,
                        text_val,
                        label,
                        burn_after_read=form.getfirst("burn_after_read", "") == "1",
                    )
                )

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

    def serve_item_file(
        self, item_id: str, *, role: str, inline: bool = False
    ) -> None:
        item = self.server.state.get_item(item_id)
        if item is None or not item.path.exists():
            return self.deny(HTTPStatus.NOT_FOUND, "Item no longer available.")
        extension = Path(item.name).suffix.lower()
        if inline and item.kind != "image" and extension not in (
            DOCUMENT_PREVIEW_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
        ):
            return self.deny(HTTPStatus.NOT_FOUND, "Preview not available for this file.")
        response_started = False
        try:
            size = item.path.stat().st_size
            start = 0
            end = size - 1
            status = HTTPStatus.OK
            byte_range = self.headers.get("Range")
            if inline and byte_range:
                try:
                    unit, value = byte_range.split("=", 1)
                    if unit != "bytes" or "," in value:
                        raise ValueError
                    first, last = value.split("-", 1)
                    if first:
                        start = int(first)
                        end = min(int(last), size - 1) if last else size - 1
                    else:
                        length = int(last)
                        start = max(0, size - length)
                    if start >= size or end < start:
                        raise ValueError
                    status = HTTPStatus.PARTIAL_CONTENT
                except ValueError:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

            claimed = self.server.state.claim_item_download(
                item_id,
                role,
                inline=inline,
            )
            if claimed is None:
                return self.deny(HTTPStatus.NOT_FOUND, "Item no longer available.")
            item, source = claimed
            self.send_response(status)
            self.send_header("Content-Type", item.mime)
            disposition = "inline" if inline else "attachment"
            encoded_name = urllib.parse.quote(item.name)
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{encoded_name}",
            )
            self.send_header("Content-Length", str(end - start + 1))
            if inline:
                self.send_header("Accept-Ranges", "bytes")
                if status == HTTPStatus.PARTIAL_CONTENT:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            response_started = True
            try:
                self.copy_file_to_response(source, start, end - start + 1)
            finally:
                source.close()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as error:
            if not response_started:
                self.send_json(
                    {"ok": False, "error": f"Could not serve this item: {error}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

    def copy_file_to_response(
        self, source: BinaryIO, offset: int, count: int
    ) -> None:
        source.seek(offset)
        remaining = count
        unsupported_sendfile_errors = {
            errno.EINVAL,
            errno.ENOSYS,
            errno.EOPNOTSUPP,
            errno.EXDEV,
        }

        if (
            os.name == "posix"
            and hasattr(os, "sendfile")
            and not isinstance(self.connection, ssl.SSLSocket)
        ):
            self.wfile.flush()
            sent = 0
            while sent < count:
                try:
                    written = os.sendfile(
                        self.connection.fileno(),
                        source.fileno(),
                        offset + sent,
                        count - sent,
                    )
                except InterruptedError:
                    continue
                except OSError as error:
                    if error.errno in unsupported_sendfile_errors:
                        break
                    raise
                if written == 0:
                    break
                sent += written
            remaining -= sent
            source.seek(offset + sent)

        while remaining > 0:
            chunk = source.read(min(MIB, remaining))
            if not chunk:
                break
            self.wfile.write(chunk)
            remaining -= len(chunk)

    def serve_received_zip(self, role: str) -> None:
        state = self.server.state
        sources: list[tuple[BinaryIO, str]] = []
        archive_path: Path | None = None
        response_started = False

        try:
            with state.lock:
                used_names: set[str] = set()
                for item in state.items:
                    if item.sender == role:
                        continue
                    try:
                        source = item.path.open("rb")
                    except FileNotFoundError:
                        continue

                    base_name = safe_name(item.name, "item")
                    stem, suffix = Path(base_name).stem, Path(base_name).suffix
                    archive_name = base_name
                    index = 2
                    while archive_name in used_names:
                        archive_name = f"{stem} ({index}){suffix}"
                        index += 1
                    used_names.add(archive_name)
                    sources.append((source, archive_name))

            if len(sources) < 2:
                return self.deny(
                    HTTPStatus.NOT_FOUND,
                    "At least two received items are required to create a ZIP.",
                )

            with tempfile.NamedTemporaryFile(
                prefix="quickshare-received-",
                suffix=".zip",
                dir=state.work,
                delete=False,
            ) as temporary:
                archive_path = Path(temporary.name)

            with zipfile.ZipFile(
                archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1
            ) as archive:
                for source, archive_name in sources:
                    info = zipfile.ZipInfo(archive_name)
                    info.compress_type = zip_compression_for_name(archive_name)
                    with archive.open(info, "w", force_zip64=True) as destination:
                        shutil.copyfileobj(source, destination, length=MIB)

            size = archive_path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition",
                "attachment; filename=\"quickshare-received.zip\"",
            )
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            response_started = True
            with archive_path.open("rb") as source:
                self.copy_file_to_response(source, 0, size)
        except OSError as error:
            if not response_started:
                return self.deny(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"Could not create the ZIP download: {error}",
                )
        finally:
            for source, _ in sources:
                source.close()
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)

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
        return f"{self.server.state.scheme}://{self.server.state.mdns_host}:{self.server.server_port}{self.phone_path()}"

    def phone_ip_url(self) -> str:
        return f"{self.server.state.scheme}://{self.server.state.bind_ip}:{self.server.server_port}{self.phone_path()}"

    def laptop_url(self) -> str:
        host = self.server.state.mdns_host if self.server.state.scheme == "https" else "localhost"
        return f"{self.server.state.scheme}://{host}:{self.server.server_port}{self.admin_path()}"

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
        elif filename.endswith(".json"):
            ctype = "application/json; charset=utf-8"
        elif filename.endswith(".svg"):
            ctype = "image/svg+xml"
        elif filename.endswith(".png"):
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_text(target.read_text(encoding="utf-8"), HTTPStatus.OK, ctype)

    def serve_service_worker(self) -> None:
        target = Path(__file__).with_name("frontend") / "service-worker.js"
        if not target.is_file():
            return self.deny(HTTPStatus.NOT_FOUND, "Service worker not found.")
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Service-Worker-Allowed", "/")
        self.end_headers()
        self.wfile.write(body)

    def serve_manifest(self) -> None:
        phone_path = self.phone_path()
        manifest = {
            "id": phone_path,
            "name": "QuickShare",
            "short_name": "QuickShare",
            "description": "Private, local file sharing between your laptop and phone.",
            "start_url": phone_path,
            "scope": "/",
            "display": "standalone",
            "background_color": "#0b0f19",
            "theme_color": "#0b0f19",
            "icons": [
                {
                    "src": "/static/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": "/static/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any",
                },
            ],
            "share_target": {
                "action": f"{phone_path}/share-target",
                "method": "POST",
                "enctype": "multipart/form-data",
                "params": {
                    "title": "title",
                    "text": "text",
                    "url": "url",
                    "files": [
                        {
                            "name": "files",
                            "accept": ["image/*", "video/*", "audio/*", "application/pdf"],
                        }
                    ],
                },
            },
        }
        body = json.dumps(manifest).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
            "freeDiskBytes": shutil.disk_usage(state.work).free,
            "saveInbox": state.save_inbox,
            "scratchpad": state.scratchpad,
            "scratchpadRevision": state.scratchpad_revision,
            "pinEnabled": bool(state.pin),
            "remainingSeconds": state.remaining_seconds(),
            "error": error,
            "items": [
                it.to_dict(base_path) for it in reversed(state.items)
            ],
        }
        state_json = json.dumps(initial_state, ensure_ascii=False).replace("</", "<\\/")
        title = "QuickShare • Laptop Hub" if role == "laptop" else "QuickShare • Mobile Hub"
        manifest_url = f"{base_path}/manifest.json" if role == "phone" else "/static/manifest.json"
        template_path = Path(__file__).with_name("frontend") / "index.html"
        template_html = template_path.read_text(encoding="utf-8")
        return (
            template_html.replace("__PAGE_TITLE__", html.escape(title))
            .replace("__INITIAL_STATE_JSON__", state_json)
            .replace("__MANIFEST_URL__", html.escape(manifest_url, quote=True))
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
        "--pin",
        help="Require a 4-digit PIN before phones can access this session",
    )
    parser.add_argument("--tls-cert", help="TLS certificate PEM file (enables HTTPS)")
    parser.add_argument("--tls-key", help="TLS private key PEM file (enables HTTPS)")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the laptop browser",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional files or folders to pre-share (also accepts: send PATH...)",
    )
    args = parser.parse_args()
    if args.paths and args.paths[0] == "send":
        args.paths = args.paths[1:]
        if not args.paths:
            parser.error("send requires at least one file or folder")
    if args.pin is not None and (
        len(args.pin) != 4 or any(char < "0" or char > "9" for char in args.pin)
    ):
        parser.error("--pin must contain exactly four digits")
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert and --tls-key must be provided together")
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
        args.mdns_host = mdns.active_hostname
        args.scheme = "https" if args.tls_cert else "http"
        state = ShareState(args, bind_ip, Path(work))
        try:
            for path_arg in args.paths:
                state.add_local_path(Path(path_arg).expanduser().resolve())
        except (OSError, ValueError) as error:
            mdns.stop()
            parser.error(str(error))
        listen_host = args.bind if args.bind else "0.0.0.0"
        try:
            server = QuickShareServer((listen_host, args.port), state)
        except OSError:
            server = QuickShareServer((listen_host, 0), state)
        if args.tls_cert:
            import ssl

            tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            try:
                tls_context.load_cert_chain(args.tls_cert, args.tls_key)
            except (OSError, ssl.SSLError) as error:
                mdns.stop()
                server.server_close()
                parser.error(f"Could not load TLS certificate/key: {error}")
            server.socket = tls_context.wrap_socket(server.socket, server_side=True)
        args.mdns_host = mdns.start(server.server_port)
        state.mdns_host = args.mdns_host
        admin_route = f"/laptop/{state.admin_token}" if state.admin_token else "/laptop"
        phone_route = f"/phone/{state.phone_token}" if state.phone_token else "/phone"
        laptop_url = f"{state.scheme}://{state.mdns_host if state.scheme == 'https' else 'localhost'}:{server.server_port}{admin_route}"
        laptop_mdns_url = f"{state.scheme}://{state.mdns_host}:{server.server_port}{admin_route}"
        phone_url = f"{state.scheme}://{state.mdns_host}:{server.server_port}{phone_route}"
        phone_ip_url = f"{state.scheme}://{bind_ip}:{server.server_port}{phone_route}"
        startup_message = (
            "QuickShare is running (two-way session, no background service)."
            f"\n  Laptop URL (Wi-Fi independent): {laptop_url}  (or {laptop_mdns_url})"
            f"\n  Phone URL  (Wi-Fi independent): {phone_url}"
            f"\n  Phone Fallback IP URL:          {phone_ip_url}"
            f"\n  Inbox directory:                 {Path.home() / 'QuickShare-Inbox'}"
        )
        if mdns.warning:
            startup_message += f"\n  Name discovery warning:         {mdns.warning}"
        if args.pin:
            startup_message += "\n  Phone access:                    PIN required"
        print(startup_message + "\nPress Ctrl+C to stop.", flush=True)
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
