from contextlib import closing, contextmanager
import re
import sqlite3
import time

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

KINDS = {"general": "お問い合わせ", "privacy": "個人情報の確認・訂正・削除", "report": "募集の通報"}
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
PAGE_SIZE = 25


def create_support(db_path, trade_db_path):
    support = Blueprint("support", __name__)

    @contextmanager
    def database(write=False):
        with closing(sqlite3.connect(db_path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db

    with database(write=True) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS support_requests (
                id INTEGER PRIMARY KEY, sender TEXT, email TEXT NOT NULL, kind TEXT NOT NULL,
                offer_id INTEGER, body TEXT NOT NULL, created INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', reply TEXT NOT NULL DEFAULT ''
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS support_requests_created ON support_requests(created)")

    @support.get("/terms")
    def terms():
        return render_template("site_policy.html", page="terms")

    @support.get("/privacy")
    def privacy():
        return render_template("site_policy.html", page="privacy")

    @support.route("/support", methods=["GET", "POST"])
    def contact():
        sender = session.get("discord_id")
        source = request.form if request.method == "POST" else request.args
        offer_id = source.get("offer", type=int)
        kind = source.get("kind", "report" if offer_id else "general")
        if kind not in KINDS:
            abort(400)
        if kind == "report":
            if not sender:
                return redirect(url_for("login"))
            if offer_id is None or not 1 <= offer_id <= 2**63 - 1:
                abort(404)
            with closing(sqlite3.connect(trade_db_path)) as trade_db:
                if not trade_db.execute("SELECT 1 FROM trade_offers WHERE id=?", (offer_id,)).fetchone():
                    abort(404)
        else:
            offer_id = None

        if request.method == "POST":
            body = request.form.get("body", "").strip()
            email = request.form.get("email", "").strip()
            if not 5 <= len(body) <= 1800 or len(email) > 254 or (email and not EMAIL.fullmatch(email)) or (not sender and not email):
                return render_template("site_support.html", kinds=KINDS, kind=kind, offer_id=offer_id,
                                       error="内容は5〜1800文字で入力してください。未ログインの場合は返信用メールアドレスも必要です。",
                                       body=body, email=email), 400
            now = int(time.time())
            with database(write=True) as db:
                if sender:
                    count = db.execute(
                        "SELECT COUNT(*) FROM support_requests WHERE sender=? AND created>?", (sender, now-3600)
                    ).fetchone()[0]
                else:
                    count = db.execute(
                        """SELECT COUNT(*) FROM support_requests WHERE sender IS NULL
                           AND email=? COLLATE NOCASE AND created>?""", (email, now-3600),
                    ).fetchone()[0]
                    total = db.execute(
                        "SELECT COUNT(*) FROM support_requests WHERE sender IS NULL AND created>?", (now-3600,)
                    ).fetchone()[0]
                    count = max(count, 3 if total >= 40 else 0)
                if count >= 3:
                    return render_template("site_support.html", kinds=KINDS, kind=kind, offer_id=offer_id,
                                           error="受付が混み合っています。時間をおいて再度お試しください。",
                                           body=body, email=email), 429
                db.execute(
                    "INSERT INTO support_requests(sender,email,kind,offer_id,body,created) VALUES (?,?,?,?,?,?)",
                    (sender, email, kind, offer_id, body, now),
                )
            flash("運営への連絡を受け付けました。", "success")
            return redirect(url_for("support.contact", kind=kind, offer=offer_id), code=303)

        history = []
        if sender:
            with database() as db:
                history = db.execute(
                    "SELECT id, kind, status, reply FROM support_requests WHERE sender=? ORDER BY id DESC LIMIT 20",
                    (sender,),
                ).fetchall()
        return render_template("site_support.html", kinds=KINDS, kind=kind, offer_id=offer_id, history=history)

    @support.get("/admin/support")
    def inbox():
        if not g.is_admin:
            abort(403)
        page = min(10000, max(1, request.args.get("page", 1, type=int)))
        with database() as db:
            cases = db.execute(
                """SELECT * FROM support_requests ORDER BY (status='open') DESC,id DESC LIMIT ? OFFSET ?""",
                (PAGE_SIZE+1, (page-1)*PAGE_SIZE),
            ).fetchall()
        return render_template("site_support_admin.html", cases=cases[:PAGE_SIZE], kinds=KINDS, page=page,
                               has_next=len(cases)>PAGE_SIZE, active="support_admin")

    @support.post("/admin/support/<int:case_id>")
    def update(case_id):
        if not g.is_admin:
            abort(403)
        if case_id > 2**63-1:
            abort(404)
        status = request.form.get("status")
        reply = request.form.get("reply", "").strip()
        if status not in {"open", "closed"} or len(reply)>1800:
            abort(400)
        with database(write=True) as db:
            result = db.execute("UPDATE support_requests SET status=?,reply=? WHERE id=?", (status, reply, case_id))
            if not result.rowcount:
                abort(404)
        flash("対応状況を更新しました。", "success")
        return redirect(url_for("support.inbox"), code=303)

    return support
