# QuickShare LAN

QuickShare is an on-demand sharing app for an Ubuntu laptop and an Android phone on the same Wi-Fi network. It has no background service, cloud account, or Android app.

## Use

Start QuickShare with the single keyboard shortcut. The browser opens on the laptop.

1. Choose **Receive** when your phone will send something to the laptop. A QR code appears immediately; scan it on Android and upload text, files, or folders (ZIP is the fallback).
2. Choose **Send** when the laptop will send something to the phone.
3. Choose one option: **Clipboard**, **Images**, **Files**, or **Folder**.
4. Select the item, click **Generate QR code**, and scan it on Android. Download the displayed item from the phone page.

Folders are automatically converted to ZIP files before sending. Incoming phone uploads are saved under `~/QuickShare-Inbox/<session-date>/`.

## Start manually

```bash
./quickshare
```

The app opens in the browser and stays available only while its terminal is open. Click **Stop sharing**, **Close app**, or press `Ctrl+C` to stop it. Sessions automatically stop after 60 minutes.

## Safety

- Each run uses new, unguessable control and phone-session links.
- The laptop control page is accessible only from the laptop; the QR link is limited to private LAN clients.
- Default upload limit is 500 MiB per request; change it with `--max-upload-mib`.
- Never port-forward QuickShare or use it on a public/guest network.

Both devices must be on the same Wi-Fi network without client isolation. If UFW is active, temporarily allow the port QuickShare prints, restricted to the local network, then remove that rule after sharing.
