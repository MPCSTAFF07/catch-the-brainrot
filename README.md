# catch-the-brainrot

Discordでログインしてアイテムの交換相手を探せる掲示板です。管理者は左メニューからBotの接続設定とリアクションロールも管理できます。Python 3.10以上を使用します。

## Discord側の準備

1. [Discord Developer Portal](https://discord.com/developers/applications)でアプリを作成し、Botトークンを取得します。
2. サーバーへのインストールに `bot` と `applications.commands` を設定し、Botを招待します。必要な権限は「ロールの管理」「チャンネルを見る」「メッセージ履歴を読む」「リアクションの追加」です。
3. サーバー設定で、Botのロールを付与対象のロールより上に置きます。
4. Discordの開発者モードを有効にし、サーバーIDをコピーします。

## Discordログインの準備

Discord Developer Portalのアプリで「OAuth2」を開き、Redirectsに次のURLを追加して保存します。

```text
https://catch-the-brainrot.eggkingdom.com/auth/discord/callback
```

OAuth2のClient IDとClient Secret、最初にログインする管理者のDiscord **ユーザーID**を用意します。Client SecretはEC2の初回設定で入力し、Botトークンは管理画面にログインしてから設定します。同じDiscordアプリをBotとログインに使えます。

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
  if ! .venv/bin/python -c 'from dashboard import ADMIN_FILE, read_settings; config = read_settings(ADMIN_FILE); raise SystemExit(not all(config.get(key) for key in ("admin_ids", "client_id", "client_secret")))'; then
    .venv/bin/python dashboard.py init-admin
  fi
  sudo install -m 644 deploy/catch-the-brainrot.service /etc/systemd/system/catch-the-brainrot.service
  sudo systemctl daemon-reload
  sudo systemctl enable catch-the-brainrot.service
  sudo systemctl restart catch-the-brainrot.service
)
```

初回設定では、管理者のDiscordユーザーIDとOAuth2のClient ID・Client Secretを入力します。旧パスワード版を使用していた場合も、更新時に同じ入力が必要です。Botトークンは後からWebで設定します。既存の `.env` がある環境では、Webで設定を保存するまでは既存のBot設定を読み込みます。

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

管理画面はHTTPS経由で使います。EC2側で使用する外部ポートは既存のHTTP/HTTPS用の80/443です。アプリの18791はEC2内部のループバックで待ち受けます。

## 交換掲示板

Discordでログインすると交換募集の一覧が開きます。アイテム名やカテゴリで検索し、募集詳細から交換を申し込めます。

1. 出品者は「交換に出す」で募集を投稿します。
2. 申込者は募集詳細から提案文を添えて申し込みます。「提案・取引」で出品者が承認または却下します。却下した提案文は削除され、募集は継続します。
3. 承認した取引には、BotがDiscordに当事者用テキストチャンネルを作ります。出品者と申込者がDiscordでやり取りし、ゲーム内で交換したあと、サイトで双方が「完了申請」を押すと募集が締め切られます。

「自分の募集」から未取引の募集を締め切れます。募集、提案、承認状態は `trades.sqlite3` に保存されます。Discordの取引チャット本文はサイトに取り込みません。更新時に既存の募集は「その他」カテゴリとして引き継がれます。

一般のDiscordアカウントも掲示板を利用できます。Bot設定・リアクションロール・管理者の各画面を利用できるのは、このサイトの管理者リストに登録されたDiscord IDだけです。

Discordの取引チャンネルを作るには、両者がBotの設定先のDiscordサーバーに参加し、Botにサーバーの「チャンネルの管理」権限が必要です。作成するチャンネルは `@everyone` に非表示で、当事者2人とBotに閲覧を許可します。Discordの管理者権限を持つ人はDiscordの仕様上アクセスできます。

## Webで設定

1. [管理画面](https://catch-the-brainrot.eggkingdom.com)を開き、「Login」を押します。Botを設定する場合は、登録済みの管理者アカウントでログインしてください。
2. 左の「Bot設定」でDiscordサーバーIDとトークンを入力し、「保存して接続」を押します。少し待ってから画面を更新し、「接続中」を確認します。
3. 「リアクションロール」で、既存の案内メッセージのリンクとロールを指定し、絵文字は一覧から選ぶか直接入力します。一覧には、Botが使えるサーバーのカスタム絵文字とよく使う絵文字が表示されます。Botが対象メッセージに絵文字を付けます。
4. 一般メンバーで絵文字を押すとロールが付き、通常リアクションを外すとロールも外れます。各ロールにつき登録は1つです。手動で同じロールを持っている場合も、通常リアクションを外すとロールが外れるため、通知用などの専用ロールを使ってください。

WebまたはDiscordの `/reaction-role add`、`list`、`remove` で同じ登録を管理できます。登録を解除しても、すでに付与したロールは維持されます。運用開始前やBot停止中のリアクションの変更はさかのぼって反映しません。

## メンバー数表示

Discordで表示用のボイスチャンネルを作ります。一覧に表示したまま入室を制限する場合は、`@everyone` の「チャンネルを見る」を許可し、「接続」を拒否します。Botにはそのチャンネルの「チャンネルの管理」権限を付けます。

サイトの管理メニューから「メンバー数表示」を開き、チャンネルを選んで保存します。保存時と約5分おきにDiscordの概算人数を取得し、名前が変わるときだけ `👥 MEMBERS: 123` の形式で更新します。停止してもチャンネル名はそのまま残ります。

## 管理者の追加・削除

左の「管理者」からユーザーIDを追加します。削除すると、ログイン済みでも次の画面操作から管理機能へのアクセスを失います。交換掲示板は引き続き利用できます。初期管理者を変更する場合は、EC2で `.venv/bin/python dashboard.py init-admin` を実行し、サービスを再起動してください。追加済みの管理者は維持されます。

## 旧ポート（8791）から切り替える場合

HTTPS発行済みなら、Nginxのサイト設定をテンプレートで上書きせず、稼働中の設定の接続先だけ変更します。18791が空いていることを `sudo ss -ltnp 'sport = :18791'` で確認します。見出しだけなら空いています。

```bash
(
  set -e
  cd "$HOME/catch-the-brainrot"
  git pull --ff-only
  sudo systemctl restart catch-the-brainrot.service
  curl -fsS --retry 4 --retry-connrefused --max-time 3 -o /dev/null \
    -H 'Host: catch-the-brainrot.eggkingdom.com' -H 'X-Forwarded-Proto: https' \
    http://127.0.0.1:18791/
  site=/etc/nginx/sites-available/catch-the-brainrot
  sudo test -f "$site"
  sudo sed -i 's|proxy_pass http://127\.0\.0\.1:8791;|proxy_pass http://127.0.0.1:18791;|' "$site"
  sudo nginx -t
  sudo systemctl reload nginx
)
```

## EC2の自動更新

初回だけ、次のコマンドで自動更新を有効にします。EC2が約1分おきにGitHubの `main` を確認し、新しいコミットがあるときだけ取り込んでサービスを再起動します。依存ファイルに変更がなければ、初回以降は依存関係を入れ直しません。

```bash
(
  set -e
  cd "$HOME/catch-the-brainrot"
  git pull --ff-only
  sudo install -m 755 deploy/auto-update.sh /usr/local/sbin/catch-the-brainrot-update
  sudo install -m 644 deploy/catch-the-brainrot-update.{service,timer} /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now catch-the-brainrot-update.timer
  sudo systemctl start catch-the-brainrot-update.service
)
```

EC2内の未コミットの変更やGitHubと分岐した履歴は上書きしません。Botトークン、管理者、募集・取引データはEC2内に残ります。Nginx・DNS・systemdの設定を変更する更新は、その設定の反映手順を別途実行してください。自動更新の仕組み自体を変更した場合も、上のインストール手順を再実行します。

```bash
systemctl list-timers catch-the-brainrot-update.timer
sudo journalctl -u catch-the-brainrot-update.service -n 30 --no-pager
```

## 利用ルール・個人情報・通報

ログイン前から `/terms`、`/privacy`、`/support` を開けます。Discord Developer PortalにプライバシーポリシーURLを登録する場合は `https://catch-the-brainrot.eggkingdom.com/privacy` を使います。

運営は管理メニューの「お問い合わせ・通報管理」で受付内容を確認します。ログイン利用者にはサイト内で返信できます。ログインせず送信された場合は、入力された返信先へ運営が回答します。個人情報の開示・訂正・削除の依頼は、本人確認後に運営が対応してください。問い合わせデータは `support.sqlite3` に保存されます。

## 更新とログ

初回起動手順の最初のコマンドを再実行すると、ソースと依存関係を更新しサービスを再起動します。Discordログイン設定、Botトークン、リアクションロールと交換募集のSQLiteデータはEC2内に保存されます。復旧用バックアップを取る場合は、EC2内で所有者だけが読み取れる場所に保管してください。

```bash
sudo systemctl status catch-the-brainrot.service --no-pager
sudo journalctl -u catch-the-brainrot.service -n 40 --no-pager
```
