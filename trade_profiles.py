from contextlib import closing
import re
import sqlite3
import time

from flask import abort, flash, redirect, render_template, request, session, url_for

USER_ID = re.compile(r"[1-9][0-9]{16,19}")
IMAGE_HASH = re.compile(r"(?:a_)?[0-9a-f]{32}")
WISH_LIMIT = 5
PREVIEW_LIMIT = 3
PROFILE_PAGE_SIZE = 24


def save_profile(db, user_id, name):
    db.execute(
        """INSERT INTO trade_profiles(user_id, display_name) VALUES (?, ?)
           ON CONFLICT(user_id) DO UPDATE SET display_name=excluded.display_name
           WHERE display_name != excluded.display_name""",
        (user_id, name),
    )


def save_discord_images(db, user_id, avatar=None, banner=None, discriminator=None):
    avatar = avatar if isinstance(avatar, str) and IMAGE_HASH.fullmatch(avatar) else None
    banner = banner if isinstance(banner, str) and IMAGE_HASH.fullmatch(banner) else None
    default_index = (int(user_id) >> 22) % 6
    if isinstance(discriminator, str) and re.fullmatch(r"[0-9]{4}", discriminator) and discriminator != "0000":
        default_index = int(discriminator) % 5
    db.execute(
        """UPDATE trade_profiles SET avatar_hash=?, banner_hash=?, default_avatar_index=?
           WHERE user_id=?""", (avatar, banner, default_index, user_id),
    )


def remember_profile(db_path, user_id, name, avatar=None, banner=None, discriminator=None):
    with closing(sqlite3.connect(db_path, timeout=8)) as db, db:
        save_profile(db, user_id, name)
        save_discord_images(db, user_id, avatar, banner, discriminator)


def register_profiles(board, database, fetch_profile_images=None):
    with database(write=True) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS trade_profiles (
                user_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                avatar_hash TEXT, banner_hash TEXT, default_avatar_index INTEGER,
                images_refresh_after INTEGER NOT NULL DEFAULT 0,
                images_last_attempt INTEGER NOT NULL DEFAULT 0,
                guild_banner_hash TEXT, guild_banner_id TEXT
            )
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(trade_profiles)")}
        if "avatar_hash" not in columns:
            db.execute("ALTER TABLE trade_profiles ADD COLUMN avatar_hash TEXT")
        if "banner_hash" not in columns:
            db.execute("ALTER TABLE trade_profiles ADD COLUMN banner_hash TEXT")
        if "default_avatar_index" not in columns:
            db.execute("ALTER TABLE trade_profiles ADD COLUMN default_avatar_index INTEGER")
        if "images_refresh_after" not in columns:
            db.execute("ALTER TABLE trade_profiles ADD COLUMN images_refresh_after INTEGER NOT NULL DEFAULT 0")
        if "images_last_attempt" not in columns:
            db.execute("ALTER TABLE trade_profiles ADD COLUMN images_last_attempt INTEGER NOT NULL DEFAULT 0")
        if "guild_banner_hash" not in columns:
            db.execute("ALTER TABLE trade_profiles ADD COLUMN guild_banner_hash TEXT")
            db.execute("UPDATE trade_profiles SET images_refresh_after=0")
        if "guild_banner_id" not in columns:
            db.execute("ALTER TABLE trade_profiles ADD COLUMN guild_banner_id TEXT")
        db.execute("""
            INSERT OR IGNORE INTO trade_profiles(user_id, display_name)
            SELECT owner_id, owner_name FROM trade_offers ORDER BY id DESC
        """)
        db.execute("""
            INSERT OR IGNORE INTO trade_profiles(user_id, display_name)
            SELECT buyer_id, buyer_name FROM trade_deals ORDER BY id DESC
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS trade_follows (
                follower_id TEXT NOT NULL REFERENCES trade_profiles(user_id),
                followed_id TEXT NOT NULL REFERENCES trade_profiles(user_id),
                PRIMARY KEY(follower_id, followed_id),
                CHECK(follower_id != followed_id)
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS trade_follows_reverse ON trade_follows(followed_id, follower_id)")
        db.execute("""
            CREATE TABLE IF NOT EXISTS trade_wishes (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES trade_profiles(user_id),
                character_name TEXT NOT NULL,
                name_key TEXT NOT NULL,
                UNIQUE(user_id, name_key)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS trade_likes (
                user_id TEXT NOT NULL REFERENCES trade_profiles(user_id),
                offer_id INTEGER NOT NULL REFERENCES trade_offers(id),
                PRIMARY KEY(user_id, offer_id)
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS trade_likes_by_offer ON trade_likes(offer_id, user_id)")

    def save_current_user(db):
        save_profile(db, session["discord_id"], session.get("discord_name") or session["discord_id"])

    def refresh_images(user_id, *, force=False):
        if fetch_profile_images is None:
            return None
        now = int(time.time())
        with database() as db:
            row = db.execute(
                "SELECT images_refresh_after FROM trade_profiles WHERE user_id=?", (user_id,)
            ).fetchone()
        if row is None or (not force and row["images_refresh_after"] > now):
            return None
        with database(write=True) as db:
            started = db.execute(
                """UPDATE trade_profiles SET images_refresh_after=?, images_last_attempt=?
                   WHERE user_id=? AND images_last_attempt<=? AND (? OR images_refresh_after<=?)""",
                (now + 60, now, user_id, now - 10, force, now),
            ).rowcount
        if not started:
            return None
        images = fetch_profile_images(user_id)
        if not isinstance(images, dict):
            return False
        with database(write=True) as db:
            save_discord_images(db, user_id, images.get("avatar"), images.get("banner"), images.get("discriminator"))
            if "guild_banner" in images:
                banner, guild_id = images.get("guild_banner"), images.get("guild_id")
                if not isinstance(banner, str) or not IMAGE_HASH.fullmatch(banner) or not isinstance(guild_id, str) or not USER_ID.fullmatch(guild_id):
                    banner = guild_id = None
                db.execute(
                    "UPDATE trade_profiles SET guild_banner_hash=?, guild_banner_id=? WHERE user_id=?",
                    (banner, guild_id, user_id),
                )
            db.execute(
                "UPDATE trade_profiles SET images_refresh_after=? WHERE user_id=?",
                (int(time.time()) + 900, user_id),
            )
        return True

    @board.post("/profile/refresh-images")
    def refresh_profile_images():
        user_id = session["discord_id"]
        with database(write=True) as db:
            save_current_user(db)
        updated = refresh_images(user_id, force=True)
        if updated is True:
            flash("Discordのプロフィール画像を再取得しました。", "success")
        elif updated is False:
            flash("Discordから画像を取得できませんでした。Botの接続を確認してください。", "error")
        else:
            flash("画像を確認中です。少し待ってからもう一度お試しください。", "error")
        return redirect(url_for("trade.my_profile"), code=303)

    @board.get("/profile")
    def my_profile():
        with database(write=True) as db:
            save_current_user(db)
        return view_profile(session["discord_id"])

    @board.get("/profile/<user_id>")
    def profile(user_id):
        if user_id == session["discord_id"]:
            return redirect(url_for("trade.my_profile"))
        return view_profile(user_id)

    def view_profile(user_id):
        if not USER_ID.fullmatch(user_id):
            abort(404)
        me = session["discord_id"]
        own = user_id == me
        refresh_images(user_id)
        with database() as db:
            user = db.execute(
                """SELECT display_name, avatar_hash, banner_hash, default_avatar_index, guild_banner_hash, guild_banner_id
                   FROM trade_profiles WHERE user_id=?""", (user_id,)
            ).fetchone()
            if user is None:
                abort(404)
            stats = db.execute(
                """SELECT
                   (SELECT COUNT(*) FROM trade_deals d JOIN trade_offers o ON o.id=d.offer_id
                     WHERE d.status='completed' AND (o.owner_id=:user OR d.buyer_id=:user)) AS completed,
                   (SELECT COUNT(*) FROM trade_follows WHERE followed_id=:user) AS followers,
                   (SELECT COUNT(*) FROM trade_follows WHERE follower_id=:user) AS following,
                   (SELECT COUNT(*) FROM trade_likes l JOIN trade_offers o ON o.id=l.offer_id
                     WHERE o.owner_id=:user) AS likes,
                   EXISTS(SELECT 1 FROM trade_follows WHERE follower_id=:me AND followed_id=:user) AS followed""",
                {"user": user_id, "me": me},
            ).fetchone()
            wishes = db.execute(
                "SELECT id, character_name FROM trade_wishes WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, WISH_LIMIT)
            ).fetchall()
            recent = db.execute(
                """SELECT CASE WHEN o.owner_id=:user THEN o.offered ELSE o.wanted END AS given,
                          CASE WHEN o.owner_id=:user THEN o.wanted ELSE o.offered END AS received
                   FROM trade_deals d JOIN trade_offers o ON o.id=d.offer_id
                   WHERE d.status='completed' AND (o.owner_id=:user OR d.buyer_id=:user)
                   ORDER BY o.closed_at DESC, d.id DESC LIMIT :limit""", {"user": user_id, "limit": PREVIEW_LIMIT},
            ).fetchall()
            offers = profile_offers(db, user_id, "active", PREVIEW_LIMIT + 1)
            liked = profile_offers(db, me, "liked", PREVIEW_LIMIT + 1) if own else []
        default_index = user["default_avatar_index"]
        if default_index is None:
            default_index = (int(user_id) >> 22) % 6
        return render_template(
            "trade_profile.html", uid=user_id, name=user["display_name"], own=own, stats=stats,
            avatar_hash=user["avatar_hash"], banner_hash=user["banner_hash"], default_avatar_index=default_index,
            guild_banner_hash=user["guild_banner_hash"], guild_banner_id=user["guild_banner_id"],
            wishes=wishes, recent=recent, offers=offers[:PREVIEW_LIMIT], liked=liked[:PREVIEW_LIMIT],
            more_offers=len(offers) > PREVIEW_LIMIT, more_liked=len(liked) > PREVIEW_LIMIT,
            wish_limit=WISH_LIMIT, active="trade_profile",
        )

    def profile_offers(db, user_id, section, limit, offset=0):
        if section == "liked":
            return db.execute(
                """SELECT o.id, o.offered, o.wanted FROM trade_likes l JOIN trade_offers o ON o.id=l.offer_id
                   WHERE l.user_id=? ORDER BY l.rowid DESC LIMIT ? OFFSET ?""", (user_id, limit, offset),
            ).fetchall()
        return db.execute(
            """SELECT o.id, o.offered, o.wanted FROM trade_offers o
               WHERE o.owner_id=? AND o.closed_at IS NULL ORDER BY o.id DESC LIMIT ? OFFSET ?""", (user_id, limit, offset),
        ).fetchall()

    @board.get("/profile/<user_id>/offers/<section>")
    def listing_history(user_id, section):
        if not USER_ID.fullmatch(user_id) or section not in {"active", "liked"}:
            abort(404)
        if section == "liked" and session["discord_id"] != user_id:
            abort(404)
        page = min(10000, max(1, request.args.get("page", 1, type=int)))
        with database() as db:
            if db.execute("SELECT 1 FROM trade_profiles WHERE user_id=?", (user_id,)).fetchone() is None:
                abort(404)
            rows = profile_offers(db, user_id, section, PROFILE_PAGE_SIZE + 1, (page - 1) * PROFILE_PAGE_SIZE)
        return render_template(
            "trade_profile_offers.html", uid=user_id, section=section, offers=rows[:PROFILE_PAGE_SIZE],
            page=page, has_next=len(rows) > PROFILE_PAGE_SIZE, active="trade_profile",
        )

    @board.get("/profile/<user_id>/connections")
    def connections(user_id):
        if not USER_ID.fullmatch(user_id):
            abort(404)
        following = request.args.get("view") == "following"
        mine, other = ("follower_id", "followed_id") if following else ("followed_id", "follower_id")
        page = min(10000, max(1, request.args.get("page", 1, type=int)))
        with database() as db:
            user = db.execute("SELECT display_name FROM trade_profiles WHERE user_id=?", (user_id,)).fetchone()
            if user is None:
                abort(404)
            users = db.execute(
                f"""SELECT p.user_id, p.display_name, p.avatar_hash, p.default_avatar_index FROM trade_follows f
                    JOIN trade_profiles p ON p.user_id=f.{other} WHERE f.{mine}=?
                    ORDER BY f.rowid DESC LIMIT 25 OFFSET ?""", (user_id, (page - 1)*24),
            ).fetchall()
        return render_template(
            "trade_connections.html", uid=user_id, name=user["display_name"], people=users[:24],
            following=following, page=page, has_next=len(users)>24, active="trade_profile",
        )

    @board.post("/profile/<user_id>/follow")
    def follow(user_id):
        action = request.form.get("action")
        me = session["discord_id"]
        offer_id = request.form.get("offer_id", type=int)
        if not USER_ID.fullmatch(user_id) or user_id == me or action not in {"add", "remove"}:
            abort(400)
        with database(write=True) as db:
            if db.execute("SELECT 1 FROM trade_profiles WHERE user_id=?", (user_id,)).fetchone() is None:
                abort(404)
            if offer_id is not None and (
                not 1 <= offer_id <= 2**63 - 1 or db.execute(
                    "SELECT 1 FROM trade_offers WHERE id=? AND owner_id=?", (offer_id, user_id)
                ).fetchone() is None
            ):
                abort(404)
            if action == "add":
                save_current_user(db)
                db.execute("INSERT OR IGNORE INTO trade_follows VALUES (?, ?)", (me, user_id))
            else:
                db.execute("DELETE FROM trade_follows WHERE follower_id=? AND followed_id=?", (me, user_id))
        if offer_id is not None:
            return redirect(url_for("trade.offer", offer_id=offer_id), code=303)
        return redirect(url_for("trade.profile", user_id=user_id), code=303)

    @board.post("/profile/wishes")
    def add_wish():
        name = request.form.get("character_name", "").strip()
        me = session["discord_id"]
        if not 1 <= len(name) <= 100:
            flash("欲しいキャラ名を1〜100文字で入力してください。", "error")
        else:
            with database(write=True) as db:
                save_current_user(db)
                count = db.execute("SELECT COUNT(*) FROM trade_wishes WHERE user_id=?", (me,)).fetchone()[0]
                if count >= WISH_LIMIT:
                    flash(f"欲しいキャラは{WISH_LIMIT}件まで登録できます。", "error")
                else:
                    db.execute(
                        "INSERT OR IGNORE INTO trade_wishes(user_id, character_name, name_key) VALUES (?, ?, ?)",
                        (me, name, name.casefold()),
                    )
        return redirect(url_for("trade.my_profile", _anchor="wishes"), code=303)

    @board.post("/profile/wishes/<int:wish_id>/remove")
    def remove_wish(wish_id):
        if wish_id > 2**63 - 1:
            abort(404)
        with database(write=True) as db:
            result = db.execute("DELETE FROM trade_wishes WHERE id=? AND user_id=?", (wish_id, session["discord_id"]))
            if not result.rowcount:
                abort(404)
        return redirect(url_for("trade.my_profile", _anchor="wishes"), code=303)

    @board.post("/trade/<int:offer_id>/like")
    def like_offer(offer_id):
        action = request.form.get("action")
        me = session["discord_id"]
        if action not in {"add", "remove"}:
            abort(400)
        if offer_id > 2**63 - 1:
            abort(404)
        with database(write=True) as db:
            offer = db.execute("SELECT owner_id FROM trade_offers WHERE id=?", (offer_id,)).fetchone()
            if offer is None:
                abort(404)
            if offer["owner_id"] == me:
                abort(400)
            if action == "add":
                save_current_user(db)
                db.execute("INSERT OR IGNORE INTO trade_likes VALUES (?, ?)", (me, offer_id))
            else:
                db.execute("DELETE FROM trade_likes WHERE user_id=? AND offer_id=?", (me, offer_id))
        return redirect(url_for("trade.offer", offer_id=offer_id), code=303)
