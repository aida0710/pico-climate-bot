#!/usr/bin/env python3
"""Edit locally, test, then back up and deploy over USB."""

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def test():
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"],
        cwd=ROOT,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Pico W センサーのテスト・USB反映")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("test", help="本体に接続せずテスト")
    sub.add_parser("ports", help="USBポート一覧")
    for command in ("deploy", "backup", "restore"):
        p = sub.add_parser(command)
        p.add_argument("--port", help="/dev/cu.usbmodem...（1台なら自動選択）")
        if command == "restore":
            p.add_argument(
                "snapshot", type=Path, help="backups/以下のバックアップディレクトリ"
            )
    args = parser.parse_args()
    if args.command == "test":
        test()
        return
    from tools.device import Device, ports, select_port
    from tools.workflow import backup, deploy, firmware_files, load_backup

    if args.command == "ports":
        for port in ports():
            print(port)
        return
    if args.command == "restore":
        files = load_backup(args.snapshot)
        if files.get("main.py") is None:
            raise ValueError("Backup must contain main.py")
    else:
        files = firmware_files(ROOT / "firmware")
        if args.command == "deploy":
            test()  # A failing test must stop before interrupting the device.
    port = select_port(args.port)
    print("USB:", port, flush=True)
    device = Device(port)
    try:
        if args.command == "backup":
            snapshot = backup(device, files.keys(), ROOT / "backups")
            print("Backup:", snapshot)
        else:
            snapshot, changed = deploy(device, files, ROOT / "backups")
            print("Backup:", snapshot)
            print(
                "Verified:",
                ", ".join(changed) if changed else "本体と一致（書き込み不要）",
            )
    finally:
        device.close()
        print("本体へ再起動を指示しました。通常動作へ戻ります。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        # Device traceback and file contents are never printed by the transport.
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
