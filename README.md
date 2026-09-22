# pico-climate-bot

[![Tests](https://github.com/aida0710/pico-climate-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/aida0710/pico-climate-bot/actions/workflows/tests.yml)

**Raspberry Pi Pico Wだけで動く、Discord向けの室内環境モニター。**

DHT11で温湿度、BMP180で温度・気圧を測定し、約5分ごとにDiscordの同じメッセージを更新します。直近7日間の温湿度グラフもPico内で生成します。PCや別サーバーの常時稼働、外部の画像生成サービスは不要です。

```mermaid
flowchart LR
    DHT[DHT11 / 温湿度] --> Pico[Pico W / MicroPython]
    BMP[BMP180 / 温度・気圧] --> Pico
    Pico --> History[7日間の循環履歴]
    History --> PNG[PNGグラフ生成]
    PNG --> Discord[Discord / 同じ投稿を編集]
    Pico --> Discord
```

## できること

- 最新の温湿度・気圧を表示し、480×272の温湿度グラフを差し替え。
- 最大2016件の履歴と投稿IDを本体に保存。再起動後も同じ投稿を更新。
- 時刻を確認できた後は、通信断中も測定履歴を保存。
- HTTPタイムアウト、Wi-Fi再接続、ウォッチドッグによる停止からの復旧。
- Macからテスト・バックアップ・USB転送・転送後の照合。

Discord REST APIを呼び出す投稿専用Botです。`discord.py`、Gateway接続、チャット受信・コマンド操作は使用しません。グラフは記録開始以降の実測値だけを表示し、未取得期間は空白になります。

## 機材と対応環境

| 項目 | 検証した構成 |
| --- | --- |
| ボード | Raspberry Pi Pico W / RP2040 |
| 本体 | MicroPython v1.27.0 |
| センサー | DHT11、BMP180（I²Cアドレス `0x77`） |
| 開発・USB転送 | macOS、Python 3.9以降、Git |
| ローカルテスト | Python標準ライブラリのみ |

USB転送ツールはmacOS向けです。Windows・LinuxのUSB転送、Pico 2 W、別のセンサーは未検証です。本体には `dht`、`framebuf`、`ntptime`、`urequests` が必要です。

### 配線

| センサーの信号 | Pico側のGPIO |
| --- | --- |
| DHT11 DATA | GP13 |
| BMP180 SDA | GP14 |
| BMP180 SCL | GP15 |

GPIO番号は物理ピン番号とは異なります。電源・GNDは使用するセンサーモジュールの仕様に従い、GPIO信号は3.3 Vに合わせてください。ピン指定は `firmware/main.py` にあります。

## はじめる

### 1. コードを取得・テスト

```sh
git clone https://github.com/aida0710/pico-climate-bot.git
cd pico-climate-bot
python3 pico.py test
```

追加パッケージのインストールは不要です。このテストはPicoへの接続やDiscordへの送信を行いません。

### 2. Discord Botを用意

[Discord Developer Portal](https://discord.com/developers/applications)でBotを作り、使用するサーバーへ招待します。対象のテキストチャンネルで、チャンネル閲覧・メッセージ履歴参照・メッセージ送信・埋め込みリンク・ファイル添付を許可してください。

Botトークン、Application ID、投稿先Channel IDを控えます。Botトークンは公開キーとは別の値です。Message Contentなどの特権Intentは不要です。

### 3. Picoに設定を保存

MicroPythonを導入済みのPicoへ、`config.example.py` を元にした `config.py` を保存します。Thonnyなどのファイル転送機能を使用し、本体のルートに置いてください。

```python
WIFI_SSID = 'YOUR_WIFI_SSID'
WIFI_PASSWORD = 'YOUR_WIFI_PASSWORD'
BOT_TOKEN = 'YOUR_BOT_TOKEN'
BOT_CHANNEL_ID = 'YOUR_CHANNEL_ID'
BOT_APPLICATION_ID = 'YOUR_APPLICATION_ID'
```

初期ファームウェアの書き込みは[MicroPythonの導入手順](https://docs.micropython.org/en/v1.27.0/rp2/tutorial/intro.html)を参照してください。このリポジトリの転送ツールはMicroPython自体を書き換えません。

実際の `config.py` はGitに追加しないでください。通常のdeployは本体の既存設定を保持します。誤転送防止のため `firmware/config.py` があるとdeployは停止します。

### 4. 本体へ転送

Thonnyなど他のシリアル接続を終了し、PicoをUSB接続して実行します。

```sh
python3 pico.py ports
python3 pico.py deploy
```

複数台ある場合は、`ports` に表示されたポートを指定します。

```sh
python3 pico.py deploy --port /dev/cu.usbmodemXXXX
```

転送前にテストとバックアップを行い、転送内容を照合して再起動します。起動後、Wi-Fi接続と時刻同期に成功すると最初の投稿が作られ、以降は同じ投稿を編集します。ツールによる再起動後は通常1分強の待機があります。

既存のPicoへBot設定だけを導入する方法は[運用手順](docs/operations.md)にあります。

## コード構成

| パス | 役割 |
| --- | --- |
| `firmware/main.py` | 測定ループ、時刻同期、Wi-Fi接続、停止対策 |
| `firmware/pico_discord.py` | Bot認証、初回投稿・編集、PNGの分割送信 |
| `firmware/pico_history.py` | CRC付き固定長の循環履歴 |
| `firmware/pico_chart.py` | MicroPython上のPNG生成 |
| `firmware/libs/bmp180.py` | BMP180ドライバー |
| `pico.py`、`tools/` | テスト・USB転送・バックアップ |
| `tests/` | ハードウェア不要の回帰テスト |

測定間隔は `INTERVAL_S`、メッセージ本文は `collect_message()` で変更できます。表示時刻はJSTです。開発中の作業方法は[CONTRIBUTING.md](CONTRIBUTING.md)を参照してください。

## データと動作の制約

| 本体内のファイル | 内容 |
| --- | --- |
| `climate.bin` | 最大2016件・24,192バイトの循環履歴 |
| `climate.png` | 約67 KBのグラフ。約65 KBの描画バッファで生成 |
| `discord_state.json` | 投稿IDと初回作成の試行状態 |
| `health.log`、`health.log.1` | 各4 KiB以下の稼働・エラーログ |

画像は1 KBずつ送信し、API応答の読み込みは16 KiBまでに制限します。グラフは温度・湿度の別パネルで、15分を超える欠測区間の線をつなぎません。気圧は本文のみです。

時計が初期値のままNTPに接続できない場合は、履歴保存・投稿を保留します。初回投稿の応答を失った場合は最近10件から自分の投稿を探し、特定できなければ重複作成を避けて停止します。投稿の手動削除による404でも、自動で新規投稿しません。

**通常のdeployは履歴・画像・投稿IDを変更しません。ただし、それらはプログラムの自動バックアップ対象には含まれません。**

## 検証範囲

2026-09-21の実機検証で、PNG生成、Discordへの投稿、再起動後の同一投稿編集、通常5分周期での画像差し替えを確認しました。PNG生成は約1.2秒で、連続する2周期の終了時の空きヒープはともに179,072バイトでした。

Wi-Fi再接続、HTTPタイムアウト、ウォッチドッグ復旧も検証しました。数ヶ月の連続運転は未検証です。過去の停止時のログがないため元の停止原因は断定せず、待機時間・切断復旧・時間カウンターの周回など、コードで確認できた停止要因を対策しています。

## ライセンスとクレジット

BMP180ドライバーにはSebastian Plamauer氏によるMITライセンスのコードが含まれます。著作権・ライセンス表記は[ドライバー冒頭](firmware/libs/bmp180.py)に保持しています。
