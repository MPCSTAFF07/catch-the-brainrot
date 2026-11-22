# catch-the-brainrot

## Discord リアクションロール Bot

管理者が指定したメッセージで、メンバーが通常リアクションを押すとロールが付き、外すとロールも外れます。設定はSQLiteに保存されます。Python 3.10以上を使用してください。

## Discord側の準備

1. [Discord Developer Portal](https://discord.com/developers/applications)でアプリを作成し、Botトークンを取得します。
2. サーバーへのインストールに `bot` と `applications.commands` を設定します。Botに「ロールの管理」「チャンネルを見る」「メッセージ履歴を読む」「リアクションの追加」を付けて招待します。特権インテントの設定は不要です。
3. サーバー設定でBotのロールを、付与するロールより上に移動します。対象チャンネルの権限も確認します。
4. Discordで開発者モードを有効にして、対象のサーバーIDをコピーします。

## 起動

リポジトリをクローンして `catch-the-brainrot` フォルダーを開きます。以下はWindows PowerShell用です。

```powershell
Copy-Item .env.example .env
notepad .env
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe bot.py
```

`.env` の `DISCORD_TOKEN` にBotトークン、`DISCORD_GUILD_ID` にサーバーIDを入力して保存します。トークンはチャットやGitHubに貼らず、このファイルにだけ設定してください。

Linuxでは `cp .env.example .env` でコピーして編集し、`python3 -m venv .venv`、`.venv/bin/python -m pip install -r requirements.txt`、`.venv/bin/python bot.py` を実行します。

## Discord内での設定

1. 案内メッセージを投稿し、右クリックから「メッセージリンクをコピー」を選びます。
2. 管理者が `/reaction-role add` を実行し、`message_link` にリンク、`emoji` に絵文字（例 `🔔`）、`role` に対象ロールを指定します。Botがメッセージに絵文字を付けます。カスタム絵文字は Discord の `<:name:id>` または `<a:name:id>` 形式を使います。
3. 一般メンバーで絵文字を押し、ロールが付くことを確認します。同じ絵文字をもう一度押すとロールが外れます。

`/reaction-role list` で登録を確認し、`/reaction-role remove` で登録を解除できます。解除時にはメッセージの絵文字と既に付いたロールが残ります。各ロールにつき登録は1つです。

リアクションの削除時は、手動付与された同じロールも外れます。この機能には通知用など、リアクションで管理する専用ロールを使ってください。登録前やBotの停止中の操作のさかのぼり同期、メッセージ削除やリアクション一括削除によるロール解除は行いません。通常リアクションで操作してください。

## ドメイン

関連Web機能のドメインは `eggkingdom.com` です。リアクションロールBotはDiscordに直接接続するため、ドメインやDNSの設定は不要です。
