import asyncio
import concurrent.futures
from datetime import timedelta
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
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from waitress import serve
from werkzeug.security import check_password_hash, generate_password_hash

from bot import ROOT, ReactionRoleBot, ReactionRoleCommands

ADMIN_FILE = ROOT / ".web-admin.json"
BOT_FILE = ROOT / ".web-bot.json"
LOG = logging.getLogger("dashboard")


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
                "bindings": bot.bindings() if bot else [],
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
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    failures = []
    login_lock = threading.Lock()

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
        if request.endpoint != "login" and not session.get("authenticated"):
            return redirect(url_for("login"))
        return None

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("authenticated"):
            return redirect(url_for("reactions"))
        if request.method == "POST":
            with login_lock:
                now = time.monotonic()
                failures[:] = [stamp for stamp in failures if now - stamp < 60]
                if len(failures) >= 8:
                    return render_template("login.html", error="試行回数が多いため、1分後にお試しください。"), 429
                if not check_password_hash(admin["password_hash"], request.form.get("password", "")):
                    failures.append(now)
                    return render_template("login.html", error="パスワードが一致しません。"), 401
                failures.clear()
            session.clear()
            session["csrf"] = secrets.token_urlsafe(32)
            session["authenticated"] = True
            session.permanent = True
            return redirect(url_for("reactions"), code=303)
        return render_template("login.html")

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

    @app.get("/")
    def reactions():
        return render_template("reactions.html", state=manager.call("status", {}), active="reactions")

    @app.post("/reactions/add")
    def add():
        submit("add", "リアクションロールを登録しました。")
        return redirect(url_for("reactions"), code=303)

    @app.post("/reactions/remove")
    def remove():
        submit("remove", "登録を解除しました。付与済みのロールはそのままです。")
        return redirect(url_for("reactions"), code=303)

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            submit("save", "Bot設定を保存しました。Discordへの接続状態を更新します。")
            return redirect(url_for("settings"), code=303)
        return render_template("settings.html", state=manager.call("status", {}), active="settings")

    return app


def main():
    os.umask(0o077)
    if sys.argv[1:] == ["init-admin"]:
        password = getpass.getpass("管理画面のパスワード（12文字以上）: ")
        if len(password) < 12 or password != getpass.getpass("管理画面のパスワード（確認）: "):
            raise SystemExit("パスワードの長さと確認入力を確認してください。")
        write_settings(ADMIN_FILE, {
            "password_hash": generate_password_hash(password),
            "session_key": secrets.token_urlsafe(48),
        })
        print("管理画面のパスワードを設定しました。運用中の場合はサービスを再起動してください。")
        return
    if sys.argv[1:]:
        raise SystemExit("使い方: python dashboard.py [init-admin]")
    admin = read_settings(ADMIN_FILE)
    if not admin:
        raise SystemExit("先に python dashboard.py init-admin を実行してください。")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    manager = BotManager()
    threading.Thread(target=manager.run, name="discord", daemon=True).start()
    serve(
        create_app(manager, admin),
        host="127.0.0.1",
        port=8791,
        trusted_proxy="127.0.0.1",
        trusted_proxy_headers={"x-forwarded-proto"},
        expose_tracebacks=False,
    )


if __name__ == "__main__":
    main()
