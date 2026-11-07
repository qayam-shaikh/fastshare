import json
import tempfile
import threading
import unittest
from argparse import Namespace
from pathlib import Path
from urllib import request, parse
from urllib.error import HTTPError

from quickshare import QuickShareServer, ShareState


class QuickShareServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir_context = tempfile.TemporaryDirectory(prefix="quickshare-tests-")
        self.temp_dir = Path(self.temp_dir_context.name)
        self.args = Namespace(
            bind="127.0.0.1",
            port=0,
            random_token=False,
            minutes=0,
            max_upload_mib=25,
            inbox=str(self.temp_dir / "inbox"),
            save_inbox=False,
            pin=None,
            scheme="http",
            mdns_host="quickshare.local",
            trusted_devices_file=self.temp_dir / "trusted_devices.json",
        )
        self.state = ShareState(self.args, "127.0.0.1", self.temp_dir)
        self.server = QuickShareServer(("127.0.0.1", 0), self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp_dir_context.cleanup()

    def request(self, path: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None, cookie: str | None = None):
        req = request.Request(self.base_url + path, data=data, headers=headers or {}, method=method)
        if cookie:
            req.add_unredirected_header("Cookie", cookie)
        try:
            return request.urlopen(req, timeout=10)
        except HTTPError as err:
            return err

    def test_pages_load(self) -> None:
        laptop_response = self.request("/laptop")
        self.assertEqual(laptop_response.status, 200)
        self.assertIn(b"Laptop Hub", laptop_response.read())

        phone_response = self.request("/phone")
        self.assertEqual(phone_response.status, 200)
        self.assertIn(b"Waiting for laptop approval", phone_response.read())
        self.assertEqual(self.state.screen, "workspace")

    def test_phone_approval_flow_and_scratchpad(self) -> None:
        approval_response = self.request(
            "/phone/request-approval",
            method="POST",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(approval_response.status, 200)
        cookie = approval_response.headers.get("Set-Cookie")
        self.assertIsNotNone(cookie)
        self.assertIn("quickshare_device=", cookie)

        denied_state = self.request("/phone/api/state", cookie=cookie)
        self.assertEqual(denied_state.status, 403)

        pending_payload = json.loads(self.request("/laptop/api/state").read())
        self.assertGreater(len(pending_payload["pending_devices"]), 0)
        token = pending_payload["pending_devices"][0]["id"]

        approve_response = self.request(f"/laptop/approve-device/{token}", method="POST")
        self.assertEqual(approve_response.status, 200)
        approve_payload = json.loads(approve_response.read())
        self.assertTrue(approve_payload["approved"])

        status_response = self.request("/phone/approval-status", cookie=cookie)
        self.assertEqual(status_response.status, 200)
        self.assertTrue(json.loads(status_response.read())["approved"])

        scratchpad_payload = json.dumps({"content": "draft note"}).encode("utf-8")
        scratchpad_response = self.request(
            "/phone/scratchpad",
            method="POST",
            data=scratchpad_payload,
            headers={"Content-Type": "application/json"},
            cookie=cookie,
        )
        self.assertEqual(scratchpad_response.status, 200)
        self.assertEqual(json.loads(scratchpad_response.read())["ok"], True)

        phone_state = json.loads(self.request("/phone/api/state", cookie=cookie).read())
        self.assertEqual(phone_state["scratchpad"], "draft note")

    def test_remembered_device_persists_and_can_be_forgotten(self) -> None:
        approval_response = self.request(
            "/phone/request-approval",
            method="POST",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        cookie = approval_response.headers.get("Set-Cookie")
        device_token = cookie.split(";", 1)[0].split("=", 1)[1]
        self.assertIn("Path=/", cookie)
        self.assertIn("Max-Age=315360000", cookie)
        remember_response = self.request(
            f"/laptop/remember-device/{device_token}",
            method="POST",
        )
        self.assertEqual(remember_response.status, 200)
        self.assertTrue(json.loads(remember_response.read())["approved"])

        fingerprint = self.state._device_fingerprint(device_token)
        self.assertTrue(self.args.trusted_devices_file.exists())
        self.assertEqual(self.args.trusted_devices_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(self.state.trusted_phone_devices()), 1)

        restarted_state = ShareState(self.args, "127.0.0.1", self.temp_dir)
        returned_token, is_new = restarted_state.request_phone_approval(
            device_token, "192.168.1.25", "Android test browser"
        )
        self.assertEqual(returned_token, device_token)
        self.assertFalse(is_new)
        self.assertTrue(restarted_state.phone_approval_status(device_token)["approved"])

        forget_response = self.request(
            f"/laptop/forget-device/{fingerprint}",
            method="POST",
        )
        self.assertEqual(forget_response.status, 200)
        self.assertTrue(json.loads(forget_response.read())["ok"])
        self.assertFalse(self.state.phone_approval_status(device_token)["approved"])
        self.assertTrue(self.state.phone_approval_status(device_token)["denied"])

        after_forget_state = ShareState(self.args, "127.0.0.1", self.temp_dir)
        after_forget_state.request_phone_approval(
            device_token, "192.168.1.25", "Android test browser"
        )
        self.assertFalse(after_forget_state.phone_approval_status(device_token)["approved"])

    def test_allow_once_does_not_persist(self) -> None:
        token, _ = self.state.request_phone_approval(None, "127.0.0.1", "Android test browser")
        self.assertTrue(self.state.set_phone_approval(token, True))
        self.assertEqual(self.state.trusted_phone_devices(), [])

        restarted_state = ShareState(self.args, "127.0.0.1", self.temp_dir)
        restarted_state.request_phone_approval(token, "127.0.0.1", "Android test browser")
        self.assertFalse(restarted_state.phone_approval_status(token)["approved"])

    def test_text_upload_and_burn_after_read(self) -> None:
        # complete the approval flow once for this test
        approval_response = self.request(
            "/phone/request-approval",
            method="POST",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        cookie = approval_response.headers.get("Set-Cookie")
        pending_payload = json.loads(self.request("/laptop/api/state").read())
        token = pending_payload["pending_devices"][0]["id"]
        self.request(f"/laptop/approve-device/{token}", method="POST")

        payload = parse.urlencode({"message": "secret text", "burn_after_read": "1"}).encode("utf-8")
        response = self.request(
            "/phone/text",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
            cookie=cookie,
        )
        self.assertEqual(response.status, 200)
        added = json.loads(response.read())["added"]
        self.assertEqual(len(added), 1)
        item_id = added[0]["id"]

        phone_state = json.loads(self.request("/phone/api/state", cookie=cookie).read())
        self.assertTrue(phone_state["items"][0]["burn_after_read"])
        self.assertTrue(self.state.consume_burn_item(item_id, "laptop"))
        self.assertIsNone(self.state.get_item(item_id))

    def test_zip_folder_browsing_and_download(self) -> None:
        source = self.temp_dir / "source"
        source.mkdir()
        (source / "hello.txt").write_text("hello from zip", encoding="utf-8")

        created = self.state.add_local_path(source)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].kind, "zip")

        browser = json.loads(self.request(f"/laptop/zip?id={created[0].id}").read())
        self.assertEqual(browser["ok"], True)
        self.assertGreater(len(browser["entries"]), 0)

        entry_index = browser["entries"][0]["index"]
        entry_bytes = self.request(f"/laptop/zip/file?id={created[0].id}&entry={entry_index}").read()
        self.assertIn(b"hello from zip", entry_bytes)


if __name__ == "__main__":
    unittest.main()
