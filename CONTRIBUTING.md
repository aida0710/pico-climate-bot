# 開発する

本体コードは `firmware/`、Mac向けの管理ツールは `tools/`、ハードウェア不要の回帰テストは `tests/` にあります。実際の認証情報や測定履歴はコミットしないでください。

```sh
python3 pico.py test
git diff --check
```

Python標準ライブラリだけでテストできます。GitHub ActionsでもLinux・macOSで同じテストを実行します。CIは実機やDiscordへ接続しません。

本体コードはMicroPython向けです。CPython専用ライブラリを追加せず、メモリ使用量・ウォッチドッグへの通知・通信待ちの上限を維持してください。ハードウェアに依存する変更は実機検証し、自動テストで確認できた範囲と区別して記載してください。

整形にはRuffを利用できます。開発ツールが必要なら、このディレクトリ内にuv環境を作ります。実行・テスト時には不要です。

```sh
uv venv .venv-dev
uv pip install --python .venv-dev/bin/python ruff
.venv-dev/bin/ruff format pico.py firmware tools tests
```

`ruff.toml` で既存の外部BMP180ドライバーを整形対象から除外しています。機能変更と大規模な整形は分け、既存ドライバーの著作権表示を保持してください。

USBへの反映は `python3 pico.py deploy` を使います。GitHubへのpushだけでは本体は変更されません。
