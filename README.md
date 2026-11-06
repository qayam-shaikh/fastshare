# QuickShare LAN

QuickShare is an on-demand, **two-way** file and text sharing web app for an Ubuntu laptop and a phone on the same Wi-Fi network. It requires no background service, cloud account, or mobile app install.

## Project Structure (Separated Frontend & Backend)

```text
fastshare-main/
├── quickshare              # CLI launcher script
├── quickshare.py           # Pure Python HTTP/REST backend server
├── frontend/
│   ├── index.html          # Semantic HTML layout (Home + Bento Workspace)
│   ├── styles.css          # Responsive Bento grid & Light/Dark design system
│   └── app.js              # Frontend state, live polling, XHR upload progress & cards
├── requirements.txt
└── .quickshare_vendor/     # Vendored QR code generator
```

## Key Features

- **Consistent Static URL (`http://<laptop-ip>:8765/share`):** Uses port `8765` and fixed paths (`/app` on laptop, `/share` on phone) so you can bookmark the phone URL once and open it anytime without scanning—while still keeping the QR code available on screen.
- **Two-Way Sharing in a Single Session:** Start from either **Send to Phone** or **Receive from Phone** and freely share items both ways in the same session.
- **Smart Interactive Cards (No Forced Auto-Downloads):**
  - **Text:** Displays text with a **Copy to Clipboard** button, or a **Download `.txt`** board if the text exceeds 2,000 characters.
  - **Images:** Displays an inline **image preview** (with click-to-zoom lightbox) and a **Download Image** button.
  - **ZIP & Folders:** Automatically compresses uploaded folders into a `.zip` archive and shows a **ZIP icon**, archive name, size, and **Download ZIP** button.
  - **Files:** Shows a **file icon**, file name, size, and **Download File** button.
