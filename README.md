# QuickShare LAN

QuickShare is an on-demand, **two-way** file and text sharing web app for an Ubuntu laptop and a phone on the same Wi-Fi network.

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

## Download received items together

When at least two items have been received on a device, use **Download All (ZIP)** above the session feed to download them as one archive.

## Preview documents and code

Click a `.pdf`, `.md`, `.json`, or `.py` item in the session feed to preview it in a modal without downloading it. Text and code previews are displayed as plain text.

## Live scratchpad and media previews

Use the **Live Scratchpad** tab to draft or paste text on either device; changes sync to the other device while the session is open. Received `.mp4`, `.webm`, `.mp3`, and `.wav` files have inline playback controls and still retain their download action.

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
