import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class FakeDevice:
    def __init__(self):
        self.files = {
            "main.py": b"old main",
            "libs/bmp180.py": b"old driver",
            "config.py": b"PRIVATE",
        }
        self.failed = False
        self.fail_target = None
        self.writes = []
        self.installed = []

    def read(self, name):
        return self.files.get(name)

    def stage(self, name, data):
        self.files[name + ".new"] = data
        self.writes.append(name)

    def install(self, name):
        if name == self.fail_target and not self.failed:
            self.failed = True
            raise OSError("simulated flash failure")
        self.files[name] = self.files.pop(name + ".new")
        self.installed.append(name)

    def remove(self, name):
        self.files.pop(name, None)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        module_path = ROOT / "tools/workflow.py"
        self.assertTrue(
            module_path.exists(), "USB deployment workflow is not implemented"
        )
        spec = importlib.util.spec_from_file_location("workflow", module_path)
        self.w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.w)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def test_backup_is_private_and_records_hashes(self):
        d = FakeDevice()
        p = self.w.backup(d, ["main.py", "libs/bmp180.py"], self.root)
        self.assertEqual((p / "config.py").read_bytes(), b"PRIVATE")
        self.assertEqual((p / "config.py").stat().st_mode & 0o777, 0o600)
        self.assertEqual(p.stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            self.w.load_backup(p),
            {"main.py": b"old main", "libs/bmp180.py": b"old driver"},
        )

    def test_deploy_keeps_config_and_replaces_main_last(self):
        d = FakeDevice()
        self.w.deploy(
            d, {"main.py": b"new main", "libs/bmp180.py": b"new driver"}, self.root
        )
        self.assertEqual(d.files["config.py"], b"PRIVATE")
        self.assertEqual(d.files["main.py"], b"new main")
        self.assertNotIn("config.py", d.writes)
        self.assertEqual(d.installed, ["libs/bmp180.py", "main.py"])

    def test_install_failure_rolls_back_all_files(self):
        d = FakeDevice()
        original = dict(d.files)
        d.fail_target = "main.py"
        with self.assertRaises(OSError):
            self.w.deploy(
                d, {"main.py": b"new main", "libs/bmp180.py": b"new driver"}, self.root
            )
        self.assertEqual(d.files, original)

    def test_staging_failure_leaves_original_files(self):
        class StagingFailure(FakeDevice):
            def stage(self, name, data):
                super().stage(name, data)
                if name == "main.py" and not self.failed:
                    self.failed = True
                    raise OSError("staging failed")

        d = StagingFailure()
        original = dict(d.files)
        with self.assertRaises(OSError):
            self.w.deploy(
                d, {"main.py": b"new main", "libs/bmp180.py": b"new driver"}, self.root
            )
        self.assertEqual(d.files, original)

    def test_rollback_removes_new_files(self):
        d = FakeDevice()
        original = dict(d.files)
        d.fail_target = "main.py"
        with self.assertRaises(OSError):
            self.w.deploy(
                d, {"extra.py": b"new file", "main.py": b"new main"}, self.root
            )
        self.assertEqual(d.files, original)

    def test_unchanged_firmware_does_not_write_flash(self):
        d = FakeDevice()
        self.w.deploy(
            d, {"main.py": b"old main", "libs/bmp180.py": b"old driver"}, self.root
        )
        self.assertEqual(d.writes, [])

    def test_modified_backup_is_rejected(self):
        d = FakeDevice()
        p = self.w.backup(d, ["main.py"], self.root)
        (p / "main.py").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self.w.load_backup(p)

    def test_missing_manifest_record_is_rejected(self):
        import json

        d = FakeDevice()
        p = self.w.backup(d, ["main.py"], self.root)
        manifest = json.loads((p / "manifest.json").read_text())
        del manifest["files"]["main.py"]
        (p / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.w.load_backup(p)

    def test_firmware_config_is_rejected(self):
        f = self.root / "firmware"
        f.mkdir()
        (f / "main.py").write_text("pass\n")
        (f / "config.py").write_text('password="secret"')
        with self.assertRaises(ValueError):
            self.w.firmware_files(f)

    def test_unsafe_backup_path_is_rejected(self):
        import json

        (self.root / "manifest.json").write_text(
            json.dumps({"managed": ["../oops.py"], "files": {}})
        )
        with self.assertRaises(ValueError):
            self.w.load_backup(self.root)


if __name__ == "__main__":
    unittest.main()
