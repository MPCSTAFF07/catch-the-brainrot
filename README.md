# catch-the-brainrot

Discordサーバーの管理画面です。左のメニューからBotの接続設定とリアクションロールを管理できます。Python 3.10以上を使用します。

## Discord側の準備

1. [Discord Developer Portal](https://discord.com/developers/applications)でアプリを作成し、Botトークンを取得します。
2. サーバーへのインストールに `bot` と `applications.commands` を設定し、Botを招待します。必要な権限は「ロールの管理」「チャンネルを見る」「メッセージ履歴を読む」「リアクションの追加」です。
3. サーバー設定で、Botのロールを付与対象のロールより上に置きます。
4. Discordの開発者モードを有効にし、サーバーIDをコピーします。

## EC2で起動

Ubuntuの `ubuntu` ユーザーで、EC2のSSHターミナルから実行してください。既存のNginxとCertbotを使います。作業前にDNSで `catch-the-brainrot.eggkingdom.com` のAレコードをEC2のパブリックIPv4アドレスに向けます。

```bash
(
  set -e
  sudo apt-get install -y git python3-venv
  cd "$HOME"
  if [ -d catch-the-brainrot/.git ]; then
    git -C catch-the-brainrot pull --ff-only
  else
    git clone https://github.com/MPCSTAFF07/catch-the-brainrot.git
  fi
  cd catch-the-brainrot
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  if [ ! -f .web-admin.json ]; then
    .venv/bin/python dashboard.py init-admin
  fi
  sudo install -m 644 deploy/catch-the-brainrot.service /etc/systemd/system/catch-the-brainrot.service
  sudo systemctl daemon-reload
  sudo systemctl enable catch-the-brainrot.service
  sudo systemctl restart catch-the-brainrot.service
)
```

初回のパスワード設定はWeb管理画面にログインするためのものです。Botトークンは後からWebで設定します。既存の `.env` がある環境では、Webで設定を保存するまでは既存のBot設定を読み込みます。

DNSがEC2に向いたら、同じターミナルでこのサブドメイン用のNginx設定とHTTPSを有効にします。既存のサブドメインと同じ名前を使っている場合は、先にその設定を確認してください。

```bash
(
  set -e
  cd "$HOME/catch-the-brainrot"
  sudo install -m 644 deploy/brainrot.nginx /etc/nginx/sites-available/catch-the-brainrot
  sudo ln -sfn /etc/nginx/sites-available/catch-the-brainrot /etc/nginx/sites-enabled/catch-the-brainrot
  sudo nginx -t
  sudo systemctl reload nginx
  sudo certbot --nginx --redirect -d catch-the-brainrot.eggkingdom.com
)
```

管理画面はHTTPS経由で使います。EC2側で使用する外部ポートは既存のHTTP/HTTPS用の80/443です。アプリの8791はEC2内部のループバックで待ち受けます。

## Webで設定

1. [管理画面](https://catch-the-brainrot.eggkingdom.com)を開き、設定した管理パスワードでログインします。
2. 左の「Bot設定」でDiscordサーバーIDとトークンを入力し、「保存して接続」を押します。少し待ってから画面を更新し、「接続中」を確認します。
3. 「リアクションロール」で、既存の案内メッセージのリンク、絵文字、ロールを入力して登録します。Botが対象メッセージに絵文字を付けます。
4. 一般メンバーで絵文字を押すとロールが付き、通常リアクションを外すとロールも外れます。各ロールにつき登録は1つです。手動で同じロールを持っている場合も、通常リアクションを外すとロールが外れるため、通知用などの専用ロールを使ってください。

WebまたはDiscordの `/reaction-role add`、`list`、`remove` で同じ登録を管理できます。登録を解除しても、すでに付与したロールは維持されます。運用開始前やBot停止中のリアクションの変更はさかのぼって反映しません。

## 更新とログ

初回起動手順の最初のコマンドを再実行すると、ソースと依存関係を更新しサービスを再起動します。Web管理者設定、Botトークン、SQLiteの設定データはEC2内に保存されます。復旧用バックアップを取る場合は、EC2内で所有者だけが読み取れる場所に保管してください。

```bash
sudo systemctl status catch-the-brainrot.service --no-pager
sudo journalctl -u catch-the-brainrot.service -n 40 --no-pager
```
