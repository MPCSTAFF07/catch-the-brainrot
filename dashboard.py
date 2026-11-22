import asyncio
import concurrent.futures
from datetime import timedelta
from functools import wraps
import getpass
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time

import discord
from dotenv import dotenv_values
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from waitress import serve
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bot import ROOT, ReactionRoleBot, ReactionRoleCommands
from trades import create_trade_board
from trade_profiles import remember_profile

ADMIN_FILE = ROOT / ".web-admin.json"
BOT_FILE = ROOT / ".web-bot.json"
TRADE_DB = ROOT / "trades.sqlite3"
LOG = logging.getLogger("dashboard")
CALLBACK_URL = "https://catch-the-brainrot.eggkingdom.com/auth/discord/callback"
DISCORD_API = "https://discord.com/api/v10"
DISCORD_ID = re.compile(r"[1-9][0-9]{16,19}")

DISCORD_USER_AGENT = "DiscordBot (https://github.com/MPCSTAFF07/catch-the-brainrot, 1.0)"
OAUTH_ERRORS = {"invalid_client", "invalid_grant", "invalid_request", "invalid_scope",
                "unauthorized_client", "unsupported_grant_type"}


class DiscordLoginError(Exception):
    def __init__(self, stage, reason, status=None):
        self.stage = stage
        self.reason = reason
        self.status = status


def discord_json(stage, api_request):
    api_request.add_header("User-Agent", DISCORD_USER_AGENT)
    api_request.add_header("Accept", "application/json")
    try:
        with urlopen(api_request, timeout=8) as response:
            payload = json.load(response)
    except HTTPError as error:
        reason = "http_error"
        try:
            body = json.loads(error.read(4096))
            value = body.get("error") if isinstance(body, dict) else None
            if isinstance(value, str) and value in OAUTH_ERRORS:
                reason = value
        except (OSError, ValueError):
            pass
        finally:
            error.close()
        raise DiscordLoginError(stage, reason, error.code) from None
    except OSError:
        raise DiscordLoginError(stage, "network_error") from None
    except ValueError:
        raise DiscordLoginError(stage, "invalid_response") from None
    if not isinstance(payload, dict):
        raise DiscordLoginError(stage, "invalid_response")
    return payload


def discord_identity(admin, code):
    data = urlencode({
        "client_id": admin["client_id"],
        "client_secret": admin["client_secret"],
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK_URL,
    }).encode()
    exchange = Request(
        f"{DISCORD_API}/oauth2/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    access_token = discord_json("token", exchange).get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise DiscordLoginError("token", "invalid_response")
    profile = Request(
        f"{DISCORD_API}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return discord_json("profile", profile)


def read_settings(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_settings(path, values):
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False
    ) as file:
        temporary_path = file.name
        try:
            json.dump(values, file)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary_path, path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)


class BotManager:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.lock = asyncio.Lock()
        self.bot = None
        self.gateway = None
        self.settings = read_settings(BOT_FILE)
        legacy = dotenv_values(ROOT / ".env")
        self.legacy_guild = str(legacy.get("DISCORD_GUILD_ID") or "").strip()
        if not self.settings and legacy.get("DISCORD_TOKEN") and self.legacy_guild.isdecimal():
            self.settings = {"token": legacy["DISCORD_TOKEN"], "guild_id": self.legacy_guild}

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self.restore())
        self.loop.run_forever()

    async def restore(self):
        async with self.lock:
            if self.settings:
                try:
                    await asyncio.wait_for(self.replace(self.settings, save=False), timeout=25)
                except Exception as error:
                    LOG.warning("Botの初回接続に失敗しました: %s", type(error).__name__)

    async def connect_gateway(self, bot):
        try:
            await bot.connect(reconnect=True)
        except Exception as error:
            LOG.warning("Discordゲートウェイ接続終了: %s", type(error).__name__)

    async def replace(self, settings, *, save):
        guild_id = settings["guild_id"]
        database_path = ROOT / f"reaction_roles-{guild_id}.sqlite3"
        if guild_id == self.legacy_guild and (ROOT / "reaction_roles.sqlite3").exists():
            database_path = ROOT / "reaction_roles.sqlite3"
        database = sqlite3.connect(database_path)
        bot = ReactionRoleBot(int(guild_id), database)
        bot.tree.add_command(ReactionRoleCommands(bot))
        try:
            await bot.login(settings["token"])
            if save:
                write_settings(BOT_FILE, settings)
        except BaseException:
            await bot.close()
            database.close()
            raise

        old_bot, old_gateway = self.bot, self.gateway
        if old_bot is not None:
            await old_bot.close()
            await asyncio.gather(old_gateway, return_exceptions=True)
            old_bot.database.close()
        self.settings = settings
        self.bot = bot
        self.gateway = asyncio.create_task(self.connect_gateway(bot))

    async def execute(self, action, form):
        if action == "profile_images":
            bot = self.bot
            if bot is None or bot.is_closed():
                return None
            user_id = int(form["user_id"])
            guild = bot.get_guild(bot.guild_id)
            calls = [asyncio.wait_for(bot.fetch_user(user_id), timeout=4)]
            if guild is not None and not guild.unavailable:
                calls.append(asyncio.wait_for(guild.fetch_member(user_id), timeout=4))
            results = await asyncio.gather(*calls, return_exceptions=True)
            user = results[0]
            if isinstance(user, Exception):
                LOG.info("Discordプロフィール画像を再取得できませんでした: %s", type(user).__name__)
                return None
            images = {
                "avatar": user.avatar.key if user.avatar else None,
                "banner": user.banner.key if user.banner else None,
                "discriminator": user.discriminator,
            }
            if len(results) > 1:
                member = results[1]
                if isinstance(member, discord.NotFound):
                    images["guild_banner"] = None
                    images["guild_id"] = None
                elif not isinstance(member, Exception):
                    images["guild_banner"] = member.guild_banner.key if member.guild_banner else None
                    images["guild_id"] = str(guild.id)
            return images

        async with self.lock:
            if action == "save":
                guild_id = form.get("guild_id", "").strip()
                token = form.get("token", "").strip() or self.settings.get("token")
                if not re.fullmatch(r"[0-9]{17,20}", guild_id) or not token:
                    raise ValueError("BotトークンとDiscordサーバーIDを入力してください。")
                try:
                    await asyncio.wait_for(self.replace({"guild_id": guild_id, "token": token}, save=True), timeout=25)
                except discord.LoginFailure as error:
                    raise ValueError("DiscordがBotトークンを認証できませんでした。") from error
                except discord.Forbidden as error:
                    raise ValueError("指定したサーバーにBotを招待し、サーバーIDを確認してください。") from error

            bot = self.bot
            guild = bot.get_guild(bot.guild_id) if bot and bot.is_ready() else None
            if action in {"counter_save", "counter_disable"}:
                if bot is None or (action == "counter_save" and guild is None):
                    raise ValueError("BotがDiscordに接続してから設定してください。")
                channel_id = form.get("channel_id", "").strip() if action == "counter_save" else ""
                if action == "counter_save" and not channel_id:
                    raise ValueError("表示するボイスチャンネルを選んでください。")
                await bot.configure_counter(guild, channel_id)

            if action == "add":
                if guild is None:
                    raise ValueError("BotがDiscordに接続してから登録してください。")
                role_id = form.get("role", "")
                role = guild.get_role(int(role_id)) if re.fullmatch(r"[0-9]{17,20}", role_id) else None
                if role is None:
                    raise ValueError("このサーバーのロールを選択してください。")
                await bot.add_binding(form.get("message_link", ""), form.get("emoji", ""), role)
            if action == "remove":
                if bot is None or not await bot.remove_binding(form.get("role", "")):
                    raise ValueError("登録が見つかりません。")

            roles = {}
            if guild is not None:
                roles = {str(role.id): role.name for role in reversed(guild.roles) if role.is_assignable()}
            return {
                "connected": guild is not None,
                "configured": bool(self.settings),
                "guild_id": self.settings.get("guild_id", ""),
                "bot_name": str(bot.user) if guild is not None else "",
                "roles": roles,
                "emojis": [
                    {"name": emoji.name, "value": str(emoji), "id": str(emoji.id)}
                    for emoji in sorted(guild.emojis, key=lambda emoji: emoji.name.casefold())
                    if emoji.is_usable()
                ] if action == "reactions" and guild is not None and guild.me is not None else [],
                "bindings": bot.bindings() if bot else [],
                "counter": bot.counter_state(guild) if action == "counter" and bot else None,
            }

    def call(self, action, values):
        future = asyncio.run_coroutine_threadsafe(self.execute(action, values), self.loop)
        try:
            return future.result(timeout=32)
        except concurrent.futures.TimeoutError as error:
            future.cancel()
            raise ValueError("Discordの応答が遅れています。画面を更新して状態を確認してください。") from error
        except discord.HTTPException as error:
            raise ValueError("Discordとの通信に失敗しました。時間をおいて再試行してください。") from error


def create_app(manager, admin):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=admin["session_key"],
        SESSION_COOKIE_NAME="brainrot_admin",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    admins_lock = threading.Lock()
    def fetch_profile_images(user_id):
        try:
            return manager.call("profile_images", {"user_id": user_id})
        except ValueError:
            return None

    app.register_blueprint(create_trade_board(TRADE_DB, fetch_profile_images))

    def allowed_ids():
        return read_settings(ADMIN_FILE).get("admin_ids", [])


    @app.before_request
    def protect():
        if not request.is_secure:
            return "HTTPSで管理画面を開いてください。", 403
        if request.endpoint == "static":
            return None
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST":
            provided = request.form.get("csrf", "")
            if not hmac.compare_digest(session["csrf"].encode(), provided.encode()):
                abort(400)
        user_id = session.get("discord_id")
        g.is_admin = bool(user_id and user_id in allowed_ids())
        if request.endpoint not in {"login", "discord_login", "discord_callback"} and not user_id:
            return redirect(url_for("login"))
        return None

    def admin_required(view):
        @wraps(view)
        def guarded(*args, **kwargs):
            if not g.is_admin:
                abort(403)
            return view(*args, **kwargs)
        return guarded

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        policy = "default-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        if request.endpoint in {"reactions", "trade.my_profile", "trade.profile"}:
            policy += "; img-src 'self' https://cdn.discordapp.com"
        response.headers["Content-Security-Policy"] = policy
        return response

    @app.get("/login")
    def login():
        if session.get("discord_id"):
            return redirect(url_for("trade.index"))
        return render_template("login.html")

    @app.get("/auth/discord")
    def discord_login():
        if session.get("discord_id"):
            return redirect(url_for("trade.index"))
        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        session["oauth_started"] = int(time.time())
        query = urlencode({
            "client_id": admin["client_id"], "redirect_uri": CALLBACK_URL,
            "response_type": "code", "scope": "identify", "state": state,
        })
        return redirect(f"https://discord.com/oauth2/authorize?{query}")

    @app.get("/auth/discord/callback")
    def discord_callback():
        expected = session.pop("oauth_state", "")
        started = session.pop("oauth_started", 0)
        received = request.args.get("state", "")
        if not expected or not hmac.compare_digest(expected.encode(), received.encode()) or not 0 <= time.time() - started < 600:
            flash("ログインの有効期限が切れました。もう一度お試しください。", "error")
            return redirect(url_for("login"), code=303)
        code = request.args.get("code")
        if not code or request.args.get("error"):
            flash("Discordでの認証が完了しませんでした。", "error")
            return redirect(url_for("login"), code=303)
        try:
            profile = discord_identity(admin, code)
            user_id = profile.get("id")
        except DiscordLoginError as error:
            LOG.warning(
                "Discordログイン失敗: stage=%s status=%s reason=%s",
                error.stage, error.status, error.reason,
            )
            if error.reason in {"invalid_client", "unauthorized_client"}:
                message = "Discordログインの設定に誤りがあります。管理者はOAuth2のClient IDとClient Secretを確認してください。"
            elif error.reason == "invalid_grant":
                message = "この認証コードを使用できませんでした。ログイン画面からやり直してください。"
            elif error.reason == "network_error":
                message = "管理画面からDiscordに接続できませんでした。時間をおいて再試行してください。"
            elif error.status == 429:
                message = "Discordへのリクエストが制限されています。少し待ってから再試行してください。"
            else:
                message = "Discordへのログインに失敗しました。管理者はサービスのログを確認してください。"
            flash(message, "error")
            return redirect(url_for("login"), code=303)
        if not isinstance(user_id, str) or not DISCORD_ID.fullmatch(user_id):
            return render_template("login.html", error="Discordアカウントを確認できませんでした。"), 403
        session.clear()
        session["discord_id"] = user_id
        session["discord_name"] = str(profile.get("global_name") or profile.get("username") or user_id)[:100]
        session["csrf"] = secrets.token_urlsafe(32)
        session.permanent = True
        remember_profile(
            TRADE_DB, user_id, session["discord_name"],
            avatar=profile.get("avatar"), banner=profile.get("banner"),
            discriminator=profile.get("discriminator"),
        )
        return redirect(url_for("trade.index"), code=303)

    @app.get("/admins")
    @admin_required
    def admins():
        return render_template("admins.html", admins=allowed_ids(), active="admins")

    @app.post("/admins/<action>")
    @admin_required
    def change_admins(action):
        if action not in {"add", "remove"}:
            abort(404)
        user_id = request.form.get("user_id", "").strip()
        if not DISCORD_ID.fullmatch(user_id):
            flash("DiscordのユーザーIDを確認してください。", "error")
            return redirect(url_for("admins"), code=303)
        with admins_lock:
            config = read_settings(ADMIN_FILE)
            ids = config["admin_ids"]
            if session["discord_id"] not in ids:
                abort(403)
            if action == "add" and user_id not in ids:
                ids.append(user_id)
                write_settings(ADMIN_FILE, config)
                flash("管理者を追加しました。", "success")
            elif action == "remove" and user_id == ids[0]:
                flash("初期管理者はEC2から変更できます。", "error")
            elif action == "remove" and user_id == session["discord_id"]:
                flash("ログイン中のアカウントはここから削除できません。", "error")
            elif action == "remove" and user_id in ids:
                ids.remove(user_id)
                write_settings(ADMIN_FILE, config)
                flash("管理者を削除しました。", "success")
            else:
                flash("管理者リストに変更はありません。", "error")
        return redirect(url_for("admins"), code=303)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"), code=303)

    def submit(action, success):
        try:
            manager.call(action, request.form.to_dict())
        except ValueError as error:
            flash(str(error), "error")
        except Exception as error:
            LOG.warning("管理画面の操作に失敗: %s", type(error).__name__)
            flash("操作に失敗しました。サーバーログを確認してください。", "error")
        else:
            flash(success, "success")

    @app.get("/reactions")
    @admin_required
    def reactions():
        return render_template("reactions.html", state=manager.call("reactions", {}), active="reactions")

    @app.post("/reactions/add")
    @admin_required
    def add():
        submit("add", "リアクションロールを登録しました。")
        return redirect(url_for("reactions"), code=303)

    @app.post("/reactions/remove")
    @admin_required
    def remove():
        submit("remove", "登録を解除しました。付与済みのロールはそのままです。")
        return redirect(url_for("reactions"), code=303)

    @app.route("/member-counter", methods=["GET", "POST"])
    @admin_required
    def member_counter():
        if request.method == "POST":
            if request.form.get("counter_action") == "disable":
                submit("counter_disable", "メンバー数の自動更新を停止しました。")
            else:
                submit("counter_save", "メンバー数の表示先を保存しました。")
            return redirect(url_for("member_counter"), code=303)
        return render_template("member_counter.html", state=manager.call("counter", {}), active="member_counter")

    @app.route("/settings", methods=["GET", "POST"])
    @admin_required
    def settings():
        if request.method == "POST":
            submit("save", "Bot設定を保存しました。Discordへの接続状態を更新します。")
            return redirect(url_for("settings"), code=303)
        return render_template("settings.html", state=manager.call("status", {}), active="settings")

    return app


def main():
    os.umask(0o077)
    admin = read_settings(ADMIN_FILE)
    if sys.argv[1:] == ["init-admin"]:
        print(f"Discord Developer Portalで登録するRedirect URL: {CALLBACK_URL}")
        owner_id = input("初期管理者のDiscordユーザーID: ").strip()
        client_id = input("Discord OAuth2 Client ID: ").strip()
        client_secret = getpass.getpass("Discord OAuth2 Client Secret: ").strip()
        if not DISCORD_ID.fullmatch(owner_id) or not DISCORD_ID.fullmatch(client_id) or not client_secret:
            raise SystemExit("ユーザーID・Client ID・Client Secretを確認してください。")
        existing = admin.get("admin_ids", [])
        write_settings(ADMIN_FILE, {
            "admin_ids": [owner_id, *(entry for entry in existing[1:] if entry != owner_id)],
            "client_id": client_id,
            "client_secret": client_secret,
            "session_key": secrets.token_urlsafe(48),
        })
        print("Discordログインを設定しました。運用中の場合はサービスを再起動してください。")
        return
    if sys.argv[1:]:
        raise SystemExit("使い方: python dashboard.py [init-admin]")
    if not admin.get("admin_ids") or not admin.get("client_id") or not admin.get("client_secret"):
        raise SystemExit("先に python dashboard.py init-admin でDiscordログインを設定してください。")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    manager = BotManager()
    threading.Thread(target=manager.run, name="discord", daemon=True).start()
    serve(
        create_app(manager, admin),
        host="127.0.0.1",
        port=18791,
        trusted_proxy="127.0.0.1",
        trusted_proxy_headers={"x-forwarded-proto"},
        expose_tracebacks=False,
    )


if __name__ == "__main__":
    main()
