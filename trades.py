from contextlib import closing
from datetime import datetime, timezone
import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

PAGE_SIZE = 24


def create_trade_board(db_path):
    board = Blueprint("trade", __name__)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS trade_offers (
                id INTEGER PRIMARY KEY,
                owner_id TEXT NOT NULL,
                owner_name TEXT NOT NULL,
                offered TEXT NOT NULL,
                wanted TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL,
                closed_at TEXT
            )
        """)

    def text_field(value, limit):
        return value.strip()[:limit]

    def now():
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @board.get("/")
    def index():
        query = text_field(request.args.get("q", ""), 100)
        mine = request.args.get("mine") == "1"
        page = min(100000, max(1, request.args.get("page", 1, type=int)))
        criteria = ["closed_at IS NULL"]
        values = []
        if mine:
            criteria.append("owner_id = ?")
            values.append(session["discord_id"])
        if query:
            criteria.append("(instr(lower(offered), lower(?)) > 0 OR instr(lower(wanted), lower(?)) > 0)")
            values.extend([query, query])
        with closing(sqlite3.connect(db_path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""SELECT id, owner_id, owner_name, offered, wanted, note
                    FROM trade_offers WHERE {' AND '.join(criteria)}
                    ORDER BY id DESC LIMIT ? OFFSET ?""",
                (*values, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
            ).fetchall()
        return render_template(
            "trade.html", offers=rows[:PAGE_SIZE], query=query, mine=mine,
            page=page, has_next=len(rows) > PAGE_SIZE, active="trade",
        )

    @board.post("/trade/post")
    def post():
        offered = text_field(request.form.get("offered", ""), 100)
        wanted = text_field(request.form.get("wanted", ""), 100)
        note = text_field(request.form.get("note", ""), 600)
        if not offered or not wanted:
            flash("出すものと欲しいものを入力してください。", "error")
        else:
            with closing(sqlite3.connect(db_path)) as db, db:
                db.execute(
                    """INSERT INTO trade_offers (owner_id, owner_name, offered, wanted, note, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (session["discord_id"], session["discord_name"], offered, wanted, note, now()),
                )
            flash("交換募集を投稿しました。", "success")
        return redirect(url_for("trade.index"), code=303)

    @board.post("/trade/<int:offer_id>/close")
    def close(offer_id):
        if offer_id > 2**63 - 1:
            abort(404)
        with closing(sqlite3.connect(db_path)) as db, db:
            result = db.execute(
                """UPDATE trade_offers SET closed_at = ?
                   WHERE id = ? AND owner_id = ? AND closed_at IS NULL""",
                (now(), offer_id, session["discord_id"]),
            )
            if not result.rowcount:
                abort(404)
        flash("交換募集を締め切りました。", "success")
        return redirect(url_for("trade.index", mine=1), code=303)

    return board
