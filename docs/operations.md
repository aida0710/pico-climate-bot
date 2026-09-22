# 運用・復旧手順

## コードを変更する

```sh
python3 pico.py test
python3 pico.py deploy
```

deployはテスト後に本体を一時停止し、管理対象ファイルと `config.py` を `backups/日時/` に保存します。各ファイルを `.new` に転送し、内容とMicroPythonでの構文を確認してから置換します。`main.py` は最後に置換します。変更のないファイルは書き込みません。

終了時は本体を再起動します。ツールによる再起動もウォッチドッグ由来として扱われることがあり、その場合は60秒待機してから接続・時刻同期を開始します。別のシリアルツールで単にCtrl-Cを送ると、ウォッチドッグによって約8秒で再起動する場合があります。

## 既存PicoへBot設定を導入する

本体にWi-Fi設定済みの `config.py` がある場合に使用します。

```sh
mkdir -p credentials
chmod 700 credentials
cp bot.example.json credentials/bot.json
chmod 600 credentials/bot.json
```

`credentials/bot.json` をエディターで開き、Botトークン・投稿先Channel ID・Application IDを入力します。その後、他のシリアル接続を閉じ、Picoを1台だけ接続して実行します。

```sh
python3 -m tools.configure_bot
```

既存のWi-Fi設定を保持してプログラムとBot設定を転送・照合します。失敗時は元の設定とプログラムの復元を試みます。通常のコード変更には `pico.py deploy` を使用してください。

BotトークンとチャンネルIDが設定されていると、旧Webhookへの投稿は行いません。Webhookモードを使う場合はBot設定を外し、本体configに `DISCORD_WEBHOOK_URL` を設定します。

## バックアップ・復元

```sh
python3 pico.py backup
python3 pico.py restore backups/BACKUP_DIRECTORY
```

バックアップには認証情報が含まれます。Git対象外で、ファイルは所有者だけが読み書きできる権限にします。外部へ共有しないでください。

`restore` はバックアップのハッシュを確認し、当時の管理対象プログラムを復元します。現在の状態も先に保存します。`config.py` や、バックアップ後に別途追加したファイルは変更しません。古いコードが現在のテストを満たすとは限りません。

履歴・投稿ID・画像は自動バックアップに含まれません。USB切断や電源断で本体にアクセスできない場合、復元は保証できません。バックアップを保持して再接続してください。

## 投稿が更新されない場合

1. 電源とWi-Fi接続、通常の約5分の更新間隔を確認します。再起動直後は追加の待機があります。
2. Botがサーバーに参加し、対象チャンネルへの必要な権限を持っていることを確認します。
3. 本体の `health.log` を確認します。`wifi_timeout` は接続待ち、`ntp_error` / `clock_wait` は時刻同期、`bot_http_error` / `bot_error` は投稿処理の失敗です。
4. シリアル出力の `BOT_HTTP` を確認します。429は待機延長、401/403は認証・権限、404は投稿またはチャンネルの削除が考えられます。

`discord_state.json` は投稿IDを保持しています。安易に削除すると既存投稿を特定できなくなるため、バックアップを取り、Discord側の投稿状態を確認してから扱ってください。自動の投稿削除・再作成は実装していません。

エラーログには例外の種類または数値コードだけを保存し、認証情報を含み得る例外本文を記録しません。同じイベントの保存は原則30分に1回です。
