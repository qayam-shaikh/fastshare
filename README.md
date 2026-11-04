# QuickShare LAN

An on-demand, two-way sharing page for an Ubuntu laptop and an Android phone on the same Wi-Fi network. It has no background service, cloud account, or Android app.

## Start

```bash
cd /home/qayam-shaikh/.codex/.chatgpt-projects/g-p-6aeb146ff2c48191b5487115ae42e468
./quickshare --clipboard
```

Or use the launcher:

```bash
./quickshare
```

The launcher starts a temporary server, opens the sharing page on Ubuntu, prints a URL, and shows a QR code in the terminal. Scan it with Android's camera. Keep the terminal open while sharing; press `Ctrl+C` or click **Stop sharing** to stop it.

Incoming items are saved under `~/QuickShare-Inbox/<session-date>/`. Files available for Android download are selected at launch or added from the laptop page.

## Common commands

```bash
# Put the current clipboard text on the share page
./quickshare --clipboard

# Make selected files downloadable by Android
./quickshare ~/Downloads/report.pdf ~/Pictures/photo.jpg

# Share a folder (it is packaged as a ZIP in a temporary directory)
./quickshare ~/Documents/project-folder

# Take a screenshot, then share it (uses gnome-screenshot or grim when available)
./quickshare --screenshot

# Pick files/folders graphically, where zenity is installed
./quickshare --pick
```

## Android use

Open the QR link in the Android browser. The page can:

- download each item the laptop selected;
- send typed/pasted text to the laptop;
- upload one or multiple files;
- use the browser's folder picker when available. Android browser support varies; if it does not offer folders, make a ZIP on the phone and upload that instead.

## Safety and limits

- A fresh, unguessable 256-bit session URL is generated every launch. There is no unauthenticated home page.
- The server binds to the selected private LAN address (not all interfaces) and rejects non-private client IP addresses by default.
- Upload names are sanitized and uploads are placed only in the session inbox. Existing names are never overwritten.
- Default maximum upload size is 500 MiB per request. Change it with `--max-upload-mib 1024`.
- The default session duration is 60 minutes. Change it with `--minutes 15`; `0` disables the timeout until stopped manually.
- The laptop page can add extra files only from the local machine; the Android page cannot browse arbitrary laptop paths.

This is designed for a trusted home/office LAN. Do not port-forward it, expose it to public Wi-Fi, or share the QR/URL with people you do not trust.

## Firewall and network checklist

Both devices must be on the same Wi-Fi/LAN, and the Wi-Fi must not use client isolation/guest isolation. If Ubuntu's UFW firewall is active, temporarily allow the displayed port only from the LAN, then remove the rule afterward:

```bash
# Example only: use the address and port printed by QuickShare.
sudo ufw allow from 192.168.1.0/24 to any port 43821 proto tcp
# After sharing
sudo ufw delete allow from 192.168.1.0/24 to any port 43821 proto tcp
```

No firewall change is normally needed when UFW is inactive. QuickShare does not run as root and does not make firewall changes itself.

## Keyboard shortcut setup

No shortcut is installed automatically. In **Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts**, add one of these commands using the absolute path to this project:

```bash
/home/qayam-shaikh/.codex/.chatgpt-projects/g-p-6aeb146ff2c48191b5487115ae42e468/quickshare --clipboard
/home/qayam-shaikh/.codex/.chatgpt-projects/g-p-6aeb146ff2c48191b5487115ae42e468/quickshare --pick
/home/qayam-shaikh/.codex/.chatgpt-projects/g-p-6aeb146ff2c48191b5487115ae42e468/quickshare --screenshot
```

Choose an unused key combination, such as `Ctrl+Alt+Q`. The `quickshare` launcher opens a terminal window when possible, because it displays the QR code and must remain running for the temporary session.

## QR support

The QR renderer is bundled project-locally in `.quickshare_vendor`; it is not installed system-wide and no network connection is needed to launch QuickShare. If that folder is intentionally removed, the tool still prints and opens the private URL and explains how to restore the optional QR package.
