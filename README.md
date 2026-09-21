# Pico W 温湿度・気圧通知

Raspberry Pi Pico W上のMicroPythonコードをMacで編集し、テストしてUSBで反映するプロジェクトです。DHT11（温湿度）とBMP180（温度・気圧）を読み、約5分ごとにDiscord Webhookへ送信します。

Python 3.9以降とGitを使用します。追加パッケージのインストールは不要です。USB操作はmacOS向けです。現在の対象は **Raspberry Pi Pico W / RP2040、MicroPython v1.27.0** です。

## 編集から反映まで

```sh
cd ~/projects/codex/pico-sensor
python3 pico.py test
python3 pico.py ports
python3 pico.py deploy
```

1. `firmware/main.py` を編集します。送信間隔は `INTERVAL_S`、本文は `collect_message()` にあります。
2. `python3 pico.py test` でテストします。このコマンドは本体を操作しません。
3. PicoをUSB接続し、`python3 pico.py deploy` で反映します。deploy自体も転送前にテストを実行します。

複数のUSB機器がある場合はポートを指定してください。

```sh
python3 pico.py deploy --port /dev/cu.usbmodem314401
```

ポート名は接続状況により変わります。Thonnyなど別のシリアル接続アプリは終了してから操作してください。

**deploy / backup / restore は測定プログラムを一時中断し、終了時に本体を再起動します。** この機体ではツールからの再起動もウォッチドッグ由来の起動理由として扱われるため、再起動後の送信開始まで通常1分強かかります。最初の送信は「時刻未同期」となり、その後は5分待機します。USB操作中はウォッチドッグを通知し続けます。別のシリアルツールでCtrl-Cを送るだけだと、約8秒で再起動することがあります。

## バックアップと復元

```sh
python3 pico.py backup
python3 pico.py restore backups/表示されたディレクトリ名
```

反映前には、対象の本体ファイルと `config.py` を `backups/日時/` に自動保存します。ファイルは所有者のみ読み書きできる権限にし、Git管理から除外します。変更のないファイルは書き込みません。

転送は `.new` へ書き込み、内容とMicroPythonでの構文を検証してから置換します。`main.py` は最後に置換します。失敗時は元のファイルへ戻して照合します。USB切断や電源断で本体と通信できない場合、自動復元は保証できません。表示されたバックアップを保持して再接続してください。

`restore` はバックアップのハッシュを確認し、**その時点の管理対象プログラム**を復元します。現在の状態も先にバックアップします。バックアップ後に別途追加したファイルや、`config.py` は変更しません。復元する古いコードが現在のテストを満たすとは限りません。

## 設定とファイル

| ファイル | 用途 |
|---|---|
| `firmware/main.py` | Wi-Fi、測定、Discord送信、復旧処理 |
| `firmware/libs/bmp180.py` | 気圧センサードライバー |
| `tests/` | ローカル回帰テスト |
| `pico.py` | 操作用CLI |
| `tools/` | USB転送、バックアップ、復元 |
| `config.example.py` | 設定項目の例。自動転送しない |
| `backups/` | 秘密情報を含むローカルバックアップ。Git対象外 |

Wi-FiのSSID・パスワード・Webhook URLは **既存Pico内の `config.py`** を使います。通常のdeployで上書きしません。`firmware/config.py` が存在すると誤転送を避けるため停止します。新しいPicoへの初期セットアップやファームウェア更新は、このツールの対象外です。

## 長期稼働への対策と検証範囲

- 時間カウンターの差は `ticks_diff()` で計算。
- Wi-Fi接続待ちは最大30秒。切断時は再接続してから送信。
- HTTPタイムアウトは5秒。応答を必ず閉じ、巨大なエラー本文を読み込まない。
- センサー異常はその周期の警告とし、次の周期で再初期化。
- 8秒のウォッチドッグで処理停止から再起動。通常の5分待機中は通知を継続。
- ウォッチドッグ再起動後は60秒待機し、最初の送信は「時刻未同期」として送信。次の周期でNTP同期を再試行。
- 本体の `health.log` / `health.log.1` にエラー・起動理由を保存。各4 KiB以下、同種イベントの保存は原則30分に1回。秘密情報を含む例外文字列は保存しない。

2026-09-21の実機検証では、Wi-Fi再接続、HTTP応答停止時のタイムアウト、NTP失敗、センサー復旧、カウンター一周の模擬試験、通信停止からのウォッチドッグ再起動、復旧直後と5分後のHTTP 204を確認しました。数ヶ月の連続運転は未検証です。

MicroPythonの古いネットワーク実装でDNSが停止する場合は、ウォッチドッグで復旧します。このプロジェクトはMicroPython本体の更新を行いません。

## Git

```sh
git diff
python3 pico.py test
git add firmware tests tools pico.py README.md
git commit -m "Describe the change"
```

GitへのコミットとPicoへの反映は別の操作です。GitHubへの公開・pushは設定していません。
