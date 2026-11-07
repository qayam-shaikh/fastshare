# QuickShare LAN

QuickShare is an on-demand, **two-way** file and text sharing web app for an Ubuntu laptop and a phone on the same Wi-Fi network.

## Wi-Fi Independent Bookmark URLs (No Hardcoded IP)

QuickShare binds to port `8765` and automatically uses **mDNS (`.local` ZeroConf/Avahi)** so your bookmark URLs stay the same even when you connect to a different Wi-Fi network or hotspot:

- **Laptop URL:** `http://localhost:8765/laptop` (or `http://quickshare.local:8765/laptop`)
- **Phone URL:** `http://quickshare.local:8765/phone` (or `http://<your-ubuntu-hostname>.local:8765/phone`)
- **Fallback IP URL:** `http://<current-wifi-ip>:8765/phone` (also shown in the UI in case a guest router blocks `.local` multicast)

> **Tip for `quickshare.local` on Ubuntu:** If `avahi-utils` is installed (`sudo apt install avahi-utils`), QuickShare automatically publishes the `quickshare.local` alias on your active Wi-Fi IP while the app is running. Otherwise, it automatically falls back to your laptop's built-in Ubuntu mDNS name (`http://<your-hostname>.local:8765/phone`).

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
