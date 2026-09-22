"""Validated firmware deployment, private backups, and best-effort rollback."""

import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def safe_name(name):
    parts = PurePosixPath(name).parts
    if (
        not parts
        or name.startswith("/")
        or any(p in (".", "..") for p in parts)
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
        or str(PurePosixPath(name)) != name
    ):
        raise ValueError("Invalid firmware path")
    return name


def firmware_files(root):
    root = Path(root)
    if (root / "config.py").exists():
        raise ValueError(
            "firmware/config.py must not contain secrets; config stays on the device"
        )
    result = {}
    for p in sorted(root.rglob("*.py")):
        name = safe_name(p.relative_to(root).as_posix())
        if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):
            raise ValueError("Symlinks are not allowed in firmware")
        data = p.read_bytes()
        if len(data) > 65536:
            raise ValueError("Firmware file exceeds 64 KiB")
        compile(data, name, "exec")
        result[name] = data
    if "main.py" not in result:
        raise ValueError("firmware/main.py is required")
    return result


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(data)
    path.chmod(0o600)


def backup(device, names, root):
    names = sorted(set(safe_name(n) for n in names))
    if "config.py" in names:
        raise ValueError("config.py is not a managed firmware file")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = root / stamp
    dest.mkdir(mode=0o700)
    records = {}
    for name in names + ["config.py"]:
        data = device.read(name)
        if data is None:
            records[name] = None
            continue
        private_write(dest / name, data)
        records[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    private_write(
        dest / "manifest.json",
        json.dumps(
            {"format": 1, "managed": names, "files": records}, indent=2
        ).encode(),
    )
    return dest


def load_backup(path):
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("format") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Invalid backup manifest")
    result = {}
    for name in manifest["managed"]:
        safe_name(name)
        if name == "config.py":
            raise ValueError("Config restoration is not allowed")
        if name not in manifest["files"]:
            raise ValueError("Backup manifest is missing a file record")
        record = manifest["files"][name]
        if record is None:
            result[name] = None
            continue
        file = path / name
        if file.is_symlink() or not file.resolve().is_relative_to(path.resolve()):
            raise ValueError("Backup path escapes its directory")
        data = file.read_bytes()
        if (
            len(data) != record["size"]
            or hashlib.sha256(data).hexdigest() != record["sha256"]
        ):
            raise ValueError("Backup integrity check failed: " + name)
        result[name] = data
    return result


def deploy(device, files, backup_root):
    for name in files:
        safe_name(name)
        if name == "config.py":
            raise ValueError("config.py must remain on the device")
    snapshot = backup(device, files.keys(), backup_root)
    original = load_backup(snapshot)
    changed = [name for name in files if original[name] != files[name]]
    order = sorted(changed, key=lambda name: (name == "main.py", name))
    try:
        # Stage and check EVERY file before replacing any live file.
        for name in order:
            if files[name] is not None:
                device.stage(name, files[name])
        for name in order:
            if files[name] is None:
                device.remove(name)
            else:
                device.install(name)
        for name in order:
            if device.read(name) != files[name]:
                raise OSError("Readback verification failed: " + name)
    except BaseException as failure:
        try:
            for name in order:
                if original[name] is None:
                    device.remove(name)
                else:
                    device.stage(name, original[name])
                    device.install(name)
                device.remove(name + ".new")
            for name in order:
                if device.read(name) != original[name]:
                    raise OSError("Rollback verification failed")
        except BaseException:
            raise RuntimeError(
                "Deployment and rollback failed. Recover from " + str(snapshot)
            ) from None
        raise failure
    return snapshot, order
