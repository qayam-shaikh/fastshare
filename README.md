# QuickShare LAN

QuickShare is an on-demand, **two-way** file and text sharing web app for an Ubuntu laptop and a phone on the same Wi-Fi network.

## Setup and start

Requirements: Ubuntu/Linux, Python 3, and a phone and laptop on the same Wi-Fi network. From a terminal in the cloned repository:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
./quickshare
```

The terminal prints the laptop and phone URLs and a QR code. Scan the QR code or open the phone URL on your phone. If the phone cannot connect, use the displayed IP fallback and check that the laptop firewall allows the selected QuickShare port (default `8765`) on your trusted local network. Run `./quickshare --help` to see options; press `Ctrl+C` or use **Stop Session** to end sharing.

Choose **Send to Phone** or **Receive from Phone** on the laptop. Both devices can send and receive throughout the active session. Files are held in the local session and cleared when the session ends; QuickShare does not upload them to a cloud service.

## Navigate the app

The workspace is split into focused pages so the sharing controls, transfer feed, scratchpad, and connection tools are not all shown together:

- **Share**: choose files, images, folders, camera capture, or text to send.
- **Transfers**: review shared items, preview supported files, and download items.
- **Scratchpad**: collaborate on a live-synced note.
- **Connect** on the laptop: scan the QR code or copy the phone link. On the phone, this page is **Controls** for laptop screenshots, media, volume, and slides.
- **Devices** on the laptop: approve new phones and manage remembered devices.

Pages have bookmarkable in-app URLs and support browser back/forward navigation. On a phone, the page navigation adapts to its available actions.

## Wi-Fi Independent Bookmark URLs (No Hardcoded IP)

QuickShare binds to port `8765` and automatically uses **mDNS (`.local` ZeroConf/Avahi)** so your bookmark URLs stay the same even when you connect to a different Wi-Fi network or hotspot:

- **Laptop URL:** `http://localhost:8765/laptop` (or `http://quickshare.local:8765/laptop`)
- **Phone URL:** `http://quickshare.local:8765/phone` (or `http://<your-ubuntu-hostname>.local:8765/phone`)
- **Fallback IP URL:** `http://<current-wifi-ip>:8765/phone` (also shown in the UI in case a guest router blocks `.local` multicast)

> **Tip for `quickshare.local` on Ubuntu:** If `avahi-utils` is installed (`sudo apt install avahi-utils`), QuickShare automatically publishes the `quickshare.local` alias on your active Wi-Fi IP while the app is running. Otherwise, it automatically falls back to your laptop's built-in Ubuntu mDNS name (`http://<your-hostname>.local:8765/phone`).

## Paste screenshots from the clipboard

On the Laptop Hub, copy a screenshot to the clipboard (for example with `PrtSc` or `Shift+PrtSc`) and press `Ctrl+V` anywhere on the page to upload it directly to the session. No need to save the screenshot as a file first. Regular text pasting is unchanged.

## Links in shared text

Shared text snippets automatically linkify web URLs, email addresses, and phone numbers. Tap a web link to open it in the mobile browser, an email address to compose a message, or a phone number to open the phone's dialer. The original text can still be copied with **Copy to Clipboard**.

When a phone shares a text snippet containing a web URL, the laptop feed also offers **Open Tab on Laptop**. It opens the validated HTTP(S) link in the laptop's default browser.

## Download received items together

When at least two items have been received on a device, use **Download All (ZIP)** above the session feed to download them as one archive.

## Preview documents and code

Click a `.pdf`, `.md`, `.json`, or `.py` item in the session feed to preview it in a modal without downloading it. Text and code previews are displayed as plain text.

## Live scratchpad and media previews

Open **Scratchpad** to draft or paste text on either device; changes sync while the session is open. Received `.mp4`, `.webm`, `.mp3`, and `.wav` files have inline playback controls and still retain their download action in **Transfers**.

## Phone capture, voice notes, and laptop controls

- On the phone, tap **Snap Photo** to open the rear camera and upload the captured image.
- Tap **Record voice note**, grant microphone access, then tap **Stop & send voice note** to upload the recording. Browser microphone APIs generally require QuickShare to be served over trusted HTTPS; the browser will also ask for microphone permission.
- On the phone, open **Controls** to request a screenshot, toggle media playback, adjust volume, or move presentation slides. The corresponding desktop tools are optional: `grim` (Wayland), `gnome-screenshot` or `scrot` (screenshots), `playerctl` (media), `pactl` (volume), and `xdotool` (slides). The UI reports when a tool is unavailable. Screenshot capture depends on the laptop's active desktop session.
- Shared folders are represented as ZIP archives. Use **Browse Contents** on a ZIP or folder card to inspect safe entries and download an individual file without downloading the whole archive.

## Approve and remember phones

On the laptop, open **Devices** from the page navigation to manage phone access. When a new phone connects, it waits for approval there:

- Choose **Allow once** to authorize the phone only until QuickShare stops.
- Choose **Remember** to allow that browser on future sessions. Saved trust is kept on the laptop in `~/.config/quickshare/trusted_devices.json` with restrictive file permissions; only a hash of the phone's browser token is stored.
- Use **Forget device** in the laptop's **Remembered devices** list to revoke and remove a saved phone. A forgotten phone must be approved again.

The phone's browser keeps its device token in a long-lived, HttpOnly cookie. Remembering therefore applies to the same browser profile and QuickShare site address. To keep trust when changing Wi-Fi, open QuickShare using its stable `.local` hostname rather than a changing fallback IP address. Clearing browser cookies/site data, using a different browser profile, or changing the site hostname/IP can require approval again. Use HTTPS on networks you do not trust; anyone with access to the phone's browser profile may use its remembered authorization.

## Send one-time text

Before sending a text snippet, optionally enable **Self-destruct after one use**. The item is removed from the session when the other device successfully copies it or downloads its text file. This is a convenience for transient sharing, not an encrypted vault: use a trusted network, and do not treat it as a substitute for end-to-end encryption or secure secret storage.

## Send files directly from the terminal

Pass one or more files or folders when launching QuickShare:

```sh
./quickshare send ~/Pictures/photo.png ~/Documents
```

Files are pre-shared in the feed, and folders are packed into ZIP files. You can also pass paths without the `send` word.

## Save received files to the laptop

On the Laptop Hub, use **Save to Inbox** on a received item to copy it directly into `~/QuickShare-Inbox` on the laptop, without downloading it through the browser.

## Optional phone PIN and upload capacity

Start QuickShare with `--pin 1234` to require that four-digit PIN when a phone first opens the session. The browser remembers authorization for that session. Uploads show live transfer speed/ETA and available laptop disk space; requests are rejected if the upload limit or available storage would be exceeded.

## Transfer performance

On Linux, file downloads use the kernel's `sendfile` path when available, with a buffered fallback for HTTPS and unsupported platforms. Folder uploads are packaged directly from the multipart temporary files instead of first expanding every file into another temporary directory. Folder ZIPs use fast compression for ordinary files and skip recompression for common media, image, and archive formats. Actual speeds still depend on the Wi-Fi link and device storage.

## Install QuickShare on a phone

The phone page provides installable-app icons, an Android share target for photos, video, audio, PDFs, links, and text, and an **Install App** button. Android browsers may show the install prompt or require choosing **Install app / Add to Home screen** from the browser menu. On iPhone or iPad, open the page in Safari, tap **Share**, then choose **Add to Home Screen**.

Mobile browsers require a **trusted HTTPS origin** for PWA installation, service workers, and native share targets. QuickShare's default HTTP mode cannot provide those browser features; enable HTTPS with a certificate trusted by the phone:

```sh
./quickshare --tls-cert /path/to/quickshare.crt --tls-key /path/to/quickshare.key
```

The certificate must be valid for the QuickShare `.local` hostname, and its issuing CA must be trusted by both devices. A self-signed certificate that has not been trusted will trigger browser warnings and will not enable PWA/share-target support. The **Install App** button explains the browser-specific next steps when an in-page install prompt is unavailable.

## Project Structure

```text
fastshare-main/
├── quickshare              # CLI launcher script
├── quickshare.py           # Pure Python HTTP/REST backend server + mDNS publisher
├── frontend/
│   ├── index.html          # Semantic HTML layout (Home + Bento Workspace)
│   ├── styles.css          # Responsive Bento grid & Light/Dark design system
│   └── app.js              # Frontend state, live polling, XHR upload progress & cards
├── requirements.txt
└── .quickshare_vendor/     # Vendored QR code generator
```
