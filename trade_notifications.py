from flask import abort, jsonify, redirect, render_template, request, session, url_for

PAGE_SIZE = 24


def notify_proposal(db, owner_id, deal_id):
    db.execute(
        "INSERT OR IGNORE INTO trade_notifications(user_id, kind, ref_id) VALUES (?, 'proposal', ?)",
        (owner_id, deal_id),
    )


def notify_approved(db, buyer_id, deal_id):
    db.execute(
        "INSERT OR IGNORE INTO trade_notifications(user_id, kind, ref_id) VALUES (?, 'approved', ?)",
        (buyer_id, deal_id),
    )


def notify_wanted_item(db, owner_id, offer_id, offered):
    db.execute(
        """INSERT OR IGNORE INTO trade_notifications(user_id, kind, ref_id)
           SELECT user_id, 'wish', ? FROM trade_wishes WHERE name_key=? AND user_id!=?""",
        (offer_id, offered.casefold(), owner_id),
    )


def register_notifications(board, database):
    with database(write=True) as db:
        schema = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='trade_notifications'"
        ).fetchone()
        migrate = schema is not None and ("'approved'" not in schema[0] or "AUTOINCREMENT" not in schema[0])
        if migrate:
            db.execute("ALTER TABLE trade_notifications RENAME TO trade_notifications_previous")
        db.execute("""
            CREATE TABLE IF NOT EXISTS trade_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES trade_profiles(user_id),
                kind TEXT NOT NULL CHECK(kind IN ('proposal','approved','wish')),
                ref_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at TEXT,
                UNIQUE(user_id, kind, ref_id)
            )
        """)
        if migrate:
            db.execute("""
                INSERT INTO trade_notifications(id, user_id, kind, ref_id, created_at, read_at)
                SELECT id, user_id, kind, ref_id, created_at, read_at FROM trade_notifications_previous
            """)
            db.execute("DROP TABLE trade_notifications_previous")
        db.execute("CREATE INDEX IF NOT EXISTS trade_notifications_user ON trade_notifications(user_id, id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS trade_wishes_match ON trade_wishes(name_key, user_id)")

    selected_columns = """
        SELECT n.id, n.kind, n.ref_id, n.created_at, n.read_at,
               o.id AS offer_id, o.offered, o.wanted, o.closed_at,
               d.status AS proposal_status, COALESCE(p.display_name,
                   CASE WHEN n.kind='proposal' THEN d.buyer_name ELSE o.owner_name END) AS actor_name,
               p.avatar_hash AS actor_avatar, p.default_avatar_index AS actor_default_avatar,
               p.user_id AS actor_id
               FROM trade_notifications n
               LEFT JOIN trade_deals d ON n.kind IN ('proposal','approved') AND d.id=n.ref_id
               LEFT JOIN trade_offers o ON o.id=CASE WHEN n.kind='wish' THEN n.ref_id ELSE d.offer_id END
               LEFT JOIN trade_profiles p ON p.user_id=CASE WHEN n.kind='proposal' THEN d.buyer_id ELSE o.owner_id END
    """

    @board.get("/notifications")
    def notifications():
        uid = session["discord_id"]
        page = min(10000, max(1, request.args.get("page", 1, type=int)))
        selected_id = request.args.get("selected", type=int)
        with database() as db:
            rows = db.execute(
                selected_columns + " WHERE n.user_id=? ORDER BY n.id DESC LIMIT ? OFFSET ?",
                (uid, PAGE_SIZE + 1, (page - 1)*PAGE_SIZE),
            ).fetchall()
            selected = None
            if selected_id is not None:
                if not 1 <= selected_id <= 2**63 - 1:
                    abort(404)
                selected = db.execute(
                    selected_columns + " WHERE n.user_id=? AND n.id=?", (uid, selected_id),
                ).fetchone()
                if selected is None:
                    abort(404)
            proposal = None
            if selected and selected["kind"] == "proposal" and selected["proposal_status"] in {"pending", "accepted"}:
                proposal = db.execute(
                    """SELECT m.body FROM trade_messages m JOIN trade_deals d ON d.id=m.deal_id
                       WHERE d.id=? AND m.sender_id=d.buyer_id ORDER BY m.id LIMIT 1""",
                    (selected["ref_id"],),
                ).fetchone()
        return render_template(
            "trade_notifications.html", rows=rows[:PAGE_SIZE], selected=selected, proposal=proposal,
            page=page, has_next=len(rows) > PAGE_SIZE, active="trade_notifications",
        )

    @board.get("/notifications/count")
    def notification_count():
        with database() as db:
            latest = db.execute(
                """SELECT COUNT(CASE WHEN read_at IS NULL THEN 1 END) AS unread, COALESCE(MAX(id),0) AS latest
                   FROM trade_notifications WHERE user_id=?""", (session["discord_id"],),
            ).fetchone()
        return jsonify(unread=latest["unread"], latest=latest["latest"])

    @board.post("/notifications/<int:notification_id>/open")
    def open_notification(notification_id):
        if notification_id > 2**63 - 1:
            abort(404)
        page = min(10000, max(1, request.form.get("page", 1, type=int)))
        with database(write=True) as db:
            result = db.execute(
                """UPDATE trade_notifications SET read_at=COALESCE(read_at,CURRENT_TIMESTAMP)
                   WHERE id=? AND user_id=?""", (notification_id, session["discord_id"]),
            )
            if not result.rowcount:
                abort(404)
        return redirect(url_for("trade.notifications", selected=notification_id, page=page if page > 1 else None), code=303)
