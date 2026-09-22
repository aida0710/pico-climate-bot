import ast
import unittest
from tools.configure_bot import updated_config


class ConfigureBotTests(unittest.TestCase):
    def test_preserves_existing_wifi_and_webhook(self):
        source = b"# keep comment\nWIFI_SSID = 'test'\nWIFI_PASSWORD = 'private-example'\nDISCORD_WEBHOOK_URL = 'https://invalid.test/example'\nBOT_TOKEN = 'old'\n"
        settings = {
            "bot_token": "new-example",
            "channel_id": "123",
            "application_id": "456",
        }
        result = updated_config(source, settings)
        self.assertIn(source.split(b"BOT_TOKEN")[0], result)
        values = {
            n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(result).body
        }
        self.assertEqual(values["BOT_TOKEN"], settings["bot_token"])
        self.assertEqual(values["WIFI_PASSWORD"], "private-example")
        self.assertEqual(updated_config(result, settings), result)
