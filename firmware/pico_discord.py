"""Single-message Discord Bot. PNG upload is streamed from flash in 1 KiB pieces."""

import os
import json
import binascii
import gc

try:
    import urequests
except ImportError:
    urequests = None

MARKER = "Pico W | 7-day climate"
API = "https://discord.com/api/v10"


def read_json(response, limit=16384, feed=lambda: None):
    data = bytearray()
    while True:
        chunk = response.raw.read(min(512, limit + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("Discord response exceeds memory limit")
        feed()
    return json.loads(data)


def sync():
    if hasattr(os, "sync"):
        os.sync()


class Dashboard:
    def __init__(
        self,
        token,
        channel_id,
        bot_id,
        state_path="discord_state.json",
        http=None,
        feed=lambda: None,
    ):
        self.token, self.channel_id, self.bot_id = token, str(channel_id), str(bot_id)
        self.state_path, self.http, self.feed = state_path, http or urequests, feed
        self.retry_after = 0
        try:
            with open(state_path) as f:
                self.state = json.load(f)
            if (
                not isinstance(self.state, dict)
                or self.state.get("channel") != self.channel_id
                or self.state.get("bot") != self.bot_id
            ):
                raise RuntimeError("Discord state does not match this channel or bot")
        except OSError as exc:
            if exc.args[0] != 2:
                raise
            self.state = {"channel": self.channel_id, "bot": self.bot_id}
        except ValueError:
            raise RuntimeError(
                "Discord state is unreadable; refusing duplicate message"
            )

    def save(self):
        with open(self.state_path + ".new", "w") as f:
            json.dump(self.state, f)
        sync()
        os.rename(self.state_path + ".new", self.state_path)
        sync()

    def headers(self):
        return {"Authorization": "Bot " + self.token, "User-Agent": "PicoSensorBot/1.0"}

    def find_existing(self):
        self.feed()
        response = self.http.request(
            "GET",
            API + "/channels/" + self.channel_id + "/messages?limit=10",
            headers=self.headers(),
            timeout=5,
        )
        try:
            if response.status_code != 200:
                raise OSError(response.status_code)
            messages = read_json(response, feed=self.feed)
        finally:
            response.close()
        found = None
        for message in messages:
            if message.get("author", {}).get("id") != self.bot_id:
                continue
            if any(
                embed.get("footer", {}).get("text") == MARKER
                for embed in message.get("embeds", [])
            ):
                if found is not None:
                    raise RuntimeError(
                        "Multiple dashboard messages; explicit selection required"
                    )
                found = message["id"]
        self.feed()
        return found

    def chunks(self, prefix, path, suffix):
        self.feed()
        yield prefix
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024)
                if not chunk:
                    break
                self.feed()  # Feed only while the finite upload is progressing.
                yield chunk
        yield suffix
        self.feed()

    def update(self, message, png_path):
        self.retry_after = 0
        if not self.state.get("message_id"):
            found = self.find_existing()
            if found:
                self.state["message_id"] = found
                self.state.pop("pending", None)
                self.save()
        message_id = self.state.get("message_id")
        if not message_id and self.state.get("pending"):
            raise RuntimeError(
                "Previous message creation unresolved; refusing duplicate"
            )
        nonce = binascii.hexlify(os.urandom(8)).decode()
        payload = {
            "content": "",
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": "お家の温度・湿度",
                    "description": message + "\n\n直近7日間の推移（未取得期間は空白）",
                    "color": 0x168C9C,
                    "footer": {"text": MARKER},
                    "image": {"url": "attachment://climate.png"},
                }
            ],
            "attachments": [
                {
                    "id": 0,
                    "filename": "climate.png",
                    "description": "直近7日間の温度と湿度。欠測区間は線をつながず表示。",
                }
            ],
        }
        if not message_id:
            payload["nonce"] = nonce
            payload["enforce_nonce"] = True
        boundary = "pico-" + nonce
        prefix = (
            (
                "--"
                + boundary
                + '\r\nContent-Disposition: form-data; name="payload_json"\r\n'
                "Content-Type: application/json\r\n\r\n"
            ).encode()
            + json.dumps(payload).encode("utf-8")
            + (
                "\r\n--"
                + boundary
                + '\r\nContent-Disposition: form-data; name="files[0]"; '
                'filename="climate.png"\r\nContent-Type: image/png\r\n\r\n'
            ).encode()
        )
        suffix = ("\r\n--" + boundary + "--\r\n").encode()
        headers = self.headers()
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        headers["Content-Length"] = str(
            len(prefix) + os.stat(png_path)[6] + len(suffix)
        )
        method = "PATCH" if message_id else "POST"
        url = API + "/channels/" + self.channel_id + "/messages"
        if message_id:
            url += "/" + message_id
        else:
            self.state["pending"] = nonce
            self.save()
        self.feed()
        gc.collect()
        response = None
        try:
            response = self.http.request(
                method,
                url,
                headers=headers,
                data=self.chunks(prefix, png_path, suffix),
                timeout=5,
            )
            status = response.status_code
            print("BOT_HTTP", status, method)
            if status == 429:
                try:
                    self.retry_after = max(
                        1,
                        int(read_json(response, feed=self.feed).get("retry_after", 300))
                        + 1,
                    )
                except Exception:
                    self.retry_after = 300
            if status not in (200, 201):
                if not message_id and 400 <= status < 500:
                    self.state.pop("pending", None)
                    self.save()
                return False
            if not message_id:
                result = read_json(response, feed=self.feed)
                self.state["message_id"] = result["id"]
                self.state.pop("pending", None)
                self.save()
            return True
        finally:
            if response is not None:
                response.close()
            self.feed()
            gc.collect()
