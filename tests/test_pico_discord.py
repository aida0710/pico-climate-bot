import importlib.util
from pathlib import Path
import tempfile
import io
import json
import unittest

ROOT = Path(__file__).resolve().parent.parent


class Response:
    def __init__(self, status=200, result=None):
        self.status_code = status
        self.result = result
        self.closed = False
        self.raw = io.BytesIO(json.dumps(result).encode())

    def json(self):
        return self.result

    def close(self):
        self.closed = True


class HTTP:
    def __init__(self):
        self.calls = []
        self.messages = []
        self.lost = False
        self.status = None
        self.responses = []

    def request(self, method, url, **kwargs):
        data = kwargs.get("data")
        body = (
            b"".join(data) if data is not None and not isinstance(data, bytes) else data
        )
        self.calls.append((method, url, kwargs, body))
        if method == "GET":
            r = Response(result=self.messages)
        elif self.status:
            r = Response(self.status, {"retry_after": 8})
        else:
            r = Response(result={"id": "444"})
            if method == "POST":
                self.messages = [
                    {
                        "id": "444",
                        "author": {"id": "111"},
                        "embeds": [{"footer": {"text": "Pico W | 7-day climate"}}],
                    }
                ]
                if self.lost:
                    raise OSError("response lost")
        self.responses.append(r)
        return r


class PicoDiscordTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "firmware/pico_discord.py"
        self.assertTrue(path.exists(), "Pico Discord client not implemented")
        spec = importlib.util.spec_from_file_location("pico_discord", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.m = m
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.png = self.path / "climate.png"
        self.png.write_bytes(b"PNG" * 3000)
        self.http = HTTP()
        self.feeds = []
        self.client = self.new_client()

    def new_client(self):
        return self.m.Dashboard(
            "secret",
            "123",
            "111",
            state_path=str(self.path / "state.json"),
            http=self.http,
            feed=lambda: self.feeds.append(1),
        )

    def test_create_once_edit_after_restart(self):
        self.assertTrue(self.client.update("reading", str(self.png)))
        self.assertTrue(self.new_client().update("reading2", str(self.png)))
        writes = [c for c in self.http.calls if c[0] != "GET"]
        self.assertEqual([c[0] for c in writes], ["POST", "PATCH"])
        self.assertTrue(writes[1][1].endswith("/messages/444"))
        self.assertTrue(all(r.closed for r in self.http.responses))
        self.assertGreater(len(self.feeds), 5)

    def test_stream_content_length_and_replacement(self):
        self.client.update("reading", str(self.png))
        method, url, args, body = self.http.calls[-1]
        self.assertEqual(int(args["headers"]["Content-Length"]), len(body))
        self.assertIn(b'"attachments"', body)
        self.assertIn(b'"files[0]"', body)
        self.assertIn(b'"allowed_mentions"', body)

    def test_response_loss_reconciles_without_duplicate(self):
        self.http.lost = True
        with self.assertRaises(OSError):
            self.client.update("reading", str(self.png))
        self.assertTrue(self.new_client().update("reading2", str(self.png)))
        self.assertEqual(sum(c[0] == "POST" for c in self.http.calls), 1)

    def test_pending_unknown_stops_new_posts(self):
        self.http.lost = True
        with self.assertRaises(OSError):
            self.client.update("reading", str(self.png))
        self.http.messages = []
        with self.assertRaises(RuntimeError):
            self.new_client().update("reading", str(self.png))
        self.assertEqual(sum(c[0] == "POST" for c in self.http.calls), 1)

    def test_oversized_response_is_bounded(self):
        self.assertTrue(hasattr(self.m, "read_json"), "bounded JSON reader missing")
        r = Response(result={"content": "x" * 50000})
        with self.assertRaises(ValueError):
            self.m.read_json(r)
        self.assertLessEqual(r.raw.tell(), 17000)

    def test_rate_limit_is_not_tight_retried(self):
        self.http.status = 429
        self.assertFalse(self.client.update("reading", str(self.png)))
        self.assertEqual(sum(c[0] == "POST" for c in self.http.calls), 1)
        self.assertGreaterEqual(self.client.retry_after, 8)

    def test_deleted_message_does_not_post_again(self):
        self.client.update("reading", str(self.png))
        self.http.status = 404
        self.assertFalse(self.client.update("reading", str(self.png)))
        self.assertEqual(sum(c[0] == "POST" for c in self.http.calls), 1)


if __name__ == "__main__":
    unittest.main()
