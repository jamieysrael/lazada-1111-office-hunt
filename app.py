import os
import io
import csv
import sqlite3
import hashlib
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, g, send_file, abort
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "1111-hunt-dev-key-change-in-prod")
app.static_folder = "public"

# ── Database ──────────────────────────────────────────────────────────

DB_PATH = os.environ.get("DATABASE_URL", "hunt.db")
if DB_PATH.startswith("sqlite:///"):
    DB_PATH = DB_PATH[10:]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA busy_timeout=20000")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS countries (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            hashtag TEXT NOT NULL,
            admin_password_hash TEXT NOT NULL,
            speed_quota INTEGER DEFAULT 30,
            hunt_start TEXT,
            hunt_end TEXT,
            mechanics_text TEXT DEFAULT '',
            first_clue TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS stops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_code TEXT NOT NULL,
            stop_number INTEGER NOT NULL,
            location_name TEXT NOT NULL,
            question TEXT NOT NULL DEFAULT '',
            answer TEXT NOT NULL DEFAULT '',
            clue_to_next TEXT NOT NULL DEFAULT '',
            is_finale INTEGER DEFAULT 0,
            finale_type TEXT DEFAULT 'team_photo',
            finale_message TEXT DEFAULT '',
            finale_task TEXT DEFAULT '',
            finale_details TEXT DEFAULT '',
            finale_hashtag TEXT DEFAULT '',
            finale_input_label TEXT DEFAULT '',
            UNIQUE(country_code, stop_number)
        );

        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_code TEXT NOT NULL,
            employee_id TEXT NOT NULL,
            name TEXT DEFAULT '',
            registered_at TEXT NOT NULL,
            UNIQUE(country_code, employee_id)
        );

        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            stop_id INTEGER NOT NULL,
            scanned_at TEXT,
            answered_at TEXT,
            attempts INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            UNIQUE(player_id, stop_id)
        );

        CREATE TABLE IF NOT EXISTS completions (
            player_id INTEGER PRIMARY KEY,
            buzz_post_url TEXT DEFAULT '',
            completed_at TEXT NOT NULL,
            total_time_seconds REAL DEFAULT 0
        );
    """)
    db.commit()


def migrate_db():
    """Add new columns to existing database if they don't exist."""
    db = get_db()
    columns = db.execute("PRAGMA table_info(stops)").fetchall()
    existing = {col["name"] for col in columns}

    new_columns = [
        ("finale_type", "TEXT DEFAULT 'team_photo'"),
        ("finale_message", "TEXT DEFAULT ''"),
        ("finale_task", "TEXT DEFAULT ''"),
        ("finale_details", "TEXT DEFAULT ''"),
        ("finale_hashtag", "TEXT DEFAULT ''"),
        ("finale_input_label", "TEXT DEFAULT ''"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in existing:
            db.execute(f"ALTER TABLE stops ADD COLUMN {col_name} {col_type}")

    db.commit()


# ── Country Config ────────────────────────────────────────────────────

DEFAULT_MECHANICS = (
    "Grab your teammates and let the hunt begin! Follow the 11.11 clues, solve questions at each pitstop, "
    "and make it to the Team Show Booth to win exclusive Lazzies and giant snacks for your team!<br><br>"
    "<strong>How to Play:</strong><br>"
    "1. <strong>Follow the Clues:</strong> Read each clue to find the next location in the office.<br>"
    "2. <strong>Solve &amp; Move:</strong> At each location, read the 11.11 question and enter your answer to unlock the next clue.<br>"
    "3. <strong>Grand Finale:</strong> Make it to the Team Show Booth for the final challenge!<br><br>"
    "<strong>Capture &amp; Share:</strong><br>"
    "Take a team photo at the final booth and share it on Buzz with <strong>#{{ hashtag }}</strong>.<br><br>"
    "<strong>Prizes:</strong><br>"
    "&bull; <strong>Speed Wins:</strong> First 30 individuals to complete the hunt get an exclusive Lazzie doll.<br>"
    "&bull; <strong>Team Wins:</strong> Top 3 Most-Liked Posts, Most Creative Team Photo, and Best Caption win a Giant Snack Attack!<br><br>"
    "<em>Hunt runs Nov 5 – Nov 10, 3:00 PM. Voting closes Nov 10 at 3:00 PM.</em>"
)

COUNTRIES = {
    "CN": {"name": "China", "flag": "🇨🇳", "hashtag": "2026BiggestSaleCN", "admin_pw": "admin1111",
           "first_clue": "The All Hands Rally kicks off the race, head down to Level One, our new VC space!"},
    "ID": {"name": "Indonesia", "flag": "🇮🇩", "hashtag": "2026BiggestSaleID", "admin_pw": "admin1111",
           "first_clue": "The All Hands Rally kicks off the race, head down to Level One, our new VC space!"},
    "MY": {"name": "Malaysia", "flag": "🇲🇾", "hashtag": "2026BiggestSaleMY", "admin_pw": "admin1111",
           "first_clue": "The All Hands Rally kicks off the race, head down to Level One, our new VC space!"},
    "PH": {"name": "Philippines", "flag": "🇵🇭", "hashtag": "2026BiggestSalePH", "admin_pw": "admin1111",
           "first_clue": "The All Hands Rally kicks off the race, head down to Level One, our new VC space!"},
    "SG": {"name": "Singapore", "flag": "🇸🇬", "hashtag": "2026BiggestSaleSG", "admin_pw": "admin1111",
           "first_clue": "The All Hands Rally kicks off the race, head down to Level One, our new VC space!"},
    "TH": {"name": "Thailand", "flag": "🇹🇭", "hashtag": "2026BiggestSaleTH", "admin_pw": "admin1111",
           "first_clue": "The All Hands Rally kicks off the race, head down to Level One, our new VC space!"},
    "VN": {"name": "Vietnam", "flag": "🇻🇳", "hashtag": "2026BiggestSaleVN", "admin_pw": "admin1111",
           "first_clue": "The All Hands Rally kicks off the race, head down to Level One, our new VC space!"},
}


def seed_countries():
    db = get_db()
    for code, info in COUNTRIES.items():
        pw_hash = hashlib.sha256(info["admin_pw"].encode()).hexdigest()
        db.execute(
            "INSERT OR IGNORE INTO countries (code, name, hashtag, admin_password_hash, mechanics_text, first_clue) VALUES (?,?,?,?,?,?)",
            (code, info["name"], info["hashtag"], pw_hash, DEFAULT_MECHANICS, info["first_clue"]),
        )
    db.commit()


def get_country(code):
    info = COUNTRIES.get(code.upper())
    if not info:
        abort(404)
    # Try to fetch DB overrides for mechanics_text and first_clue
    db = get_db()
    row = db.execute("SELECT mechanics_text, first_clue FROM countries WHERE code=?", (code.upper(),)).fetchone()
    result = {"code": code.upper(), **info}
    if row:
        if row["mechanics_text"]:
            result["mechanics_text"] = row["mechanics_text"]
        if row["first_clue"]:
            result["first_clue"] = row["first_clue"]
    return result


def seed_placeholder_stops():
    db = get_db()
    placeholders = {
        "SG": [
            ("L1 VC 1", "If \"Biggest Sale of the year\" is our campaign tagline, our internal theme is Make It __________ which means we are moving forward shoulder-to-shoulder. We are all contributing in our own way, supporting one another, and co-creating success.", "Together", "For a quiet retreat with a book in your hand, head up to Level Nine, where the library shelves stand!"),
            ("L9 Library", "Our Singapore 11.11 Campaign USPs are: 90% LazFlash Deals and $___ Must Buy Deals", "11", "Grab your team, it's time for show, head to where food bounty flows"),
            ("L9 Pantry Team Show booth", "", "", ""),
        ],
    }
    for code, stops_data in placeholders.items():
        existing = db.execute(
            "SELECT COUNT(*) as c FROM stops WHERE country_code=?", (code,)
        ).fetchone()["c"]
        if existing > 0:
            continue
        for i, (loc, q, a, clue) in enumerate(stops_data, 1):
            is_finale = 1 if i == len(stops_data) else 0
            db.execute(
                "INSERT INTO stops (country_code, stop_number, location_name, question, answer, clue_to_next, is_finale) VALUES (?,?,?,?,?,?,?)",
                (code, i, loc, q, a, clue, is_finale),
            )
    # Seed other countries with generic 3-pitstop placeholders
    for code in COUNTRIES:
        if code == "SG":
            continue
        existing = db.execute(
            "SELECT COUNT(*) as c FROM stops WHERE country_code=?", (code,)
        ).fetchone()["c"]
        if existing > 0:
            continue
        generic = [
            ("Start Point", "What does 11.11 represent?", "Singles Day", "Head to the next pitstop!"),
            ("Pitstop 2", "What year was Lazada founded?", "2012", "Almost there! Head to the Team Show Booth!"),
            ("Team Show Booth", "", "", ""),
        ]
        for i, (loc, q, a, clue) in enumerate(generic, 1):
            is_finale = 1 if i == len(generic) else 0
            db.execute(
                "INSERT INTO stops (country_code, stop_number, location_name, question, answer, clue_to_next, is_finale) VALUES (?,?,?,?,?,?,?)",
                (code, i, loc, q, a, clue, is_finale),
            )
    db.commit()


# ── Helpers ───────────────────────────────────────────────────────────

def format_duration(seconds):
    if seconds is None:
        return "--"
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"


def player_progress(player_id, country_code):
    db = get_db()
    stops = db.execute(
        "SELECT * FROM stops WHERE country_code=? ORDER BY stop_number", (country_code,)
    ).fetchall()
    progress = {}
    for row in db.execute(
        "SELECT p.*, s.stop_number FROM progress p JOIN stops s ON p.stop_id=s.id WHERE p.player_id=?",
        (player_id,),
    ).fetchall():
        progress[row["stop_number"]] = dict(row)
    return stops, progress


def admin_required(f):
    @wraps(f)
    def decorated(country_code, *args, **kwargs):
        country = get_country(country_code)
        if session.get(f"admin_{country_code}") != country["code"]:
            return redirect(f"/admin/{country_code}/login")
        return f(country_code, *args, **kwargs)
    return decorated


# ── Player Routes ─────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", countries=COUNTRIES)


@app.route("/register/<country_code>", methods=["GET", "POST"])
def register(country_code):
    country = get_country(country_code)
    error = None

    if request.method == "POST":
        employee_id = request.form.get("employee_id", "").strip()
        if not employee_id:
            error = "Please enter your Employee ID."
        else:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO players (country_code, employee_id, registered_at) VALUES (?,?,?)",
                    (country_code.upper(), employee_id, datetime.utcnow().isoformat()),
                )
                db.commit()
                player = db.execute(
                    "SELECT id FROM players WHERE country_code=? AND employee_id=?",
                    (country_code.upper(), employee_id),
                ).fetchone()
                session["player_id"] = player["id"]
                session["country_code"] = country_code.upper()
                return redirect(f"/clue/{country_code}/1")
            except sqlite3.IntegrityError:
                # Player already registered — fetch their ID and reset progress for fresh start
                player = db.execute(
                    "SELECT id FROM players WHERE country_code=? AND employee_id=?",
                    (country_code.upper(), employee_id),
                ).fetchone()
                # Clear old progress so re-registering starts fresh
                db.execute("DELETE FROM progress WHERE player_id=?", (player["id"],))
                db.execute("DELETE FROM completions WHERE player_id=?", (player["id"],))
                db.commit()
                session["player_id"] = player["id"]
                session["country_code"] = country_code.upper()
                return redirect(f"/clue/{country_code}/1")

    return render_template(
        "register.html",
        country_name=country["name"],
        country_code=country_code.upper(),
        hashtag=country["hashtag"],
        mechanics_text=country.get("mechanics_text", DEFAULT_MECHANICS),
        error=error,
    )


@app.route("/clue/<country_code>/<int:stop_num>", methods=["GET", "POST"])
def clue(country_code, stop_num):
    """Show clue and handle answer submission. No QR scanning — player reads clue,
    goes to location, reads physical question, enters answer here."""
    country = get_country(country_code)

    if "player_id" not in session:
        return redirect(f"/register/{country_code}")

    player_id = session["player_id"]
    db = get_db()

    # ── First clue (special: not from DB) ──
    if stop_num == 1:
        if request.method == "POST":
            stop_row = db.execute(
                "SELECT * FROM stops WHERE country_code=? AND stop_number=1",
                (country_code.upper(),),
            ).fetchone()
            user_answer = request.form.get("answer", "").strip().lower()
            correct_answer = stop_row["answer"].strip().lower() if stop_row else ""

            if user_answer == correct_answer:
                db.execute(
                    "INSERT INTO progress (player_id, stop_id, answered_at, attempts, completed) VALUES (?,?,?,1,1) "
                    "ON CONFLICT(player_id, stop_id) DO UPDATE SET answered_at=excluded.answered_at, completed=1",
                    (player_id, stop_row["id"], datetime.utcnow().isoformat()),
                )
                db.commit()
                return redirect(f"/clue/{country_code}/2")
            else:
                if stop_row:
                    db.execute(
                        "INSERT INTO progress (player_id, stop_id, attempts) VALUES (?,?,1) "
                        "ON CONFLICT(player_id, stop_id) DO UPDATE SET attempts = attempts + 1",
                        (player_id, stop_row["id"]),
                    )
                    db.commit()
                return render_template(
                    "wrong_answer.html",
                    country_name=country["name"],
                    country_code=country_code.upper(),
                    stop_num=1,
                    total_stops=3,
                )

        # GET — show first clue
        return render_template(
            "clue.html",
            country_name=country["name"],
            country_code=country_code.upper(),
            stop_num=1,
            total_stops=3,
            clue=country["first_clue"],
            error=None,
        )

    # ── Subsequent stops ──
    stop = db.execute(
        "SELECT * FROM stops WHERE country_code=? AND stop_number=?",
        (country_code.upper(), stop_num),
    ).fetchone()

    if not stop:
        return redirect(f"/clue/{country_code}/1")

    # For finale stops: if team_photo type, redirect to finale page; if question type, treat as regular stop
    if stop["is_finale"]:
        finale_type = stop["finale_type"] if stop["finale_type"] else "team_photo"
        if finale_type == "team_photo":
            return redirect(f"/finale/{country_code}")
        # For question type, continue to show the question below

    # Check all previous stops completed
    for sn in range(1, stop_num):
        prev_stop = db.execute(
            "SELECT id FROM stops WHERE country_code=? AND stop_number=?",
            (country_code.upper(), sn),
        ).fetchone()
        if prev_stop:
            prog = db.execute(
                "SELECT completed FROM progress WHERE player_id=? AND stop_id=?",
                (player_id, prev_stop["id"]),
            ).fetchone()
            if not prog or not prog["completed"]:
                return redirect(f"/clue/{country_code}/{sn}")

    # If already completed, redirect to next
    prog = db.execute(
        "SELECT completed FROM progress WHERE player_id=? AND stop_id=?",
        (player_id, stop["id"]),
    ).fetchone()
    if prog and prog["completed"]:
        next_stop = db.execute(
            "SELECT * FROM stops WHERE country_code=? AND stop_number=?",
            (country_code.upper(), stop_num + 1),
        ).fetchone()
        if next_stop and next_stop["is_finale"]:
            # Check finale type: question type goes to clue, team_photo goes to finale
            next_finale_type = next_stop["finale_type"] if next_stop["finale_type"] else "team_photo"
            if next_finale_type == "team_photo":
                return redirect(f"/finale/{country_code}")
            else:
                return redirect(f"/clue/{country_code}/{stop_num + 1}")
        if next_stop:
            return redirect(f"/clue/{country_code}/{stop_num + 1}")
        return redirect(f"/finale/{country_code}")

    stops, _ = player_progress(player_id, country_code.upper())
    total = len([s for s in stops if not s["is_finale"]])

    if request.method == "POST":
        user_answer = request.form.get("answer", "").strip().lower()
        correct_answer = stop["answer"].strip().lower()

        if user_answer == correct_answer:
            db.execute(
                "INSERT INTO progress (player_id, stop_id, answered_at, attempts, completed) VALUES (?,?,?,1,1) "
                "ON CONFLICT(player_id, stop_id) DO UPDATE SET answered_at=excluded.answered_at, completed=1",
                (player_id, stop["id"], datetime.utcnow().isoformat()),
            )
            db.commit()

            # Check if this stop is a question-type finale
            if stop["is_finale"]:
                finale_type = stop["finale_type"] if stop["finale_type"] else "team_photo"
                if finale_type == "question":
                    # Question-type finale completed - record completion and go to complete page
                    now = datetime.utcnow()
                    player_row = db.execute(
                        "SELECT registered_at FROM players WHERE id=?", (player_id,)
                    ).fetchone()
                    total_seconds = 0
                    if player_row:
                        reg_dt = datetime.fromisoformat(player_row["registered_at"])
                        total_seconds = (now - reg_dt).total_seconds()

                    # Check if already has a completion record
                    existing_comp = db.execute(
                        "SELECT * FROM completions WHERE player_id=?", (player_id,)
                    ).fetchone()
                    if not existing_comp:
                        db.execute(
                            "INSERT INTO completions (player_id, buzz_post_url, completed_at, total_time_seconds) VALUES (?,?,?,?)",
                            (player_id, "", now.isoformat(), total_seconds),
                        )
                        db.commit()

                    # Calculate rank
                    rank_row = db.execute(
                        "SELECT COUNT(*) + 1 as r FROM completions WHERE player_id IN (SELECT id FROM players WHERE country_code=?) AND completed_at < ?",
                        (country_code.upper(), now.isoformat()),
                    ).fetchone()
                    rank = rank_row["r"]

                    return render_template(
                        "complete.html",
                        country_name=country["name"],
                        country_code=country_code.upper(),
                        rank=rank,
                        total_time=format_duration(total_seconds),
                        speed_quota=30,
                    )

            next_stop = db.execute(
                "SELECT * FROM stops WHERE country_code=? AND stop_number=?",
                (country_code.upper(), stop_num + 1),
            ).fetchone()
            if next_stop and next_stop["is_finale"]:
                # Check finale type: question type goes to clue, team_photo goes to finale
                next_finale_type = next_stop["finale_type"] if next_stop["finale_type"] else "team_photo"
                if next_finale_type == "team_photo":
                    return redirect(f"/finale/{country_code}")
                else:
                    # Question type finale - show clue to next stop
                    return render_template(
                        "clue.html",
                        country_name=country["name"],
                        country_code=country_code.upper(),
                        stop_num=stop_num + 1,
                        total_stops=total,
                        clue=stop["clue_to_next"] if stop["clue_to_next"] else "",
                        error=None,
                    )
            if next_stop:
                return render_template(
                    "clue.html",
                    country_name=country["name"],
                    country_code=country_code.upper(),
                    stop_num=stop_num + 1,
                    total_stops=total,
                    clue=next_stop["clue_to_next"] if stop["clue_to_next"] else "",
                    error=None,
                )
            return redirect(f"/finale/{country_code}")
        else:
            db.execute(
                "UPDATE progress SET attempts = attempts + 1 WHERE player_id=? AND stop_id=?",
                (player_id, stop["id"]),
            )
            db.commit()
            return render_template(
                "wrong_answer.html",
                country_name=country["name"],
                country_code=country_code.upper(),
                stop_num=stop_num,
                total_stops=total,
            )

    # GET — show clue from previous stop
    prev_stop = db.execute(
        "SELECT * FROM stops WHERE country_code=? AND stop_number=?",
        (country_code.upper(), stop_num - 1),
    ).fetchone()
    clue_text = prev_stop["clue_to_next"] if prev_stop else ""

    return render_template(
        "clue.html",
        country_name=country["name"],
        country_code=country_code.upper(),
        stop_num=stop_num,
        total_stops=total,
        clue=clue_text,
        error=None,
    )


@app.route("/finale/<country_code>", methods=["GET", "POST"])
def finale(country_code):
    country = get_country(country_code)

    if "player_id" not in session:
        return redirect(f"/register/{country_code}")

    player_id = session["player_id"]
    db = get_db()

    # Fetch the finale stop for this country to get dynamic fields
    finale_stop = db.execute(
        "SELECT * FROM stops WHERE country_code=? AND is_finale=1 ORDER BY stop_number DESC LIMIT 1",
        (country_code.upper(),),
    ).fetchone()

    # Determine finale type (default to team_photo)
    finale_type = finale_stop["finale_type"] if finale_stop and finale_stop["finale_type"] else "team_photo"

    # Question-type finales are completed via the clue page, redirect to complete
    if finale_type == "question":
        return redirect(f"/complete/{country_code}")

    # Use finale stop fields if available, otherwise fall back to defaults
    finale_message = finale_stop["finale_message"] if finale_stop and finale_stop["finale_message"] else "You made it to the Team Show Booth!"
    finale_task = finale_stop["finale_task"] if finale_stop and finale_stop["finale_task"] else "1. Take a team photo at this booth<br>2. Post it on Buzz with the hashtag below<br>3. Paste your Buzz post URL to complete the hunt!"
    finale_details = finale_stop["finale_details"] if finale_stop and finale_stop["finale_details"] else ""
    finale_hashtag = finale_stop["finale_hashtag"] if finale_stop and finale_stop["finale_hashtag"] else country["hashtag"]
    finale_input_label = finale_stop["finale_input_label"] if finale_stop and finale_stop["finale_input_label"] else "Paste your Buzz post URL here..."

    if request.method == "POST":
        buzz_url = request.form.get("buzz_url", "").strip()
        if not buzz_url:
            return render_template(
                "finale.html",
                country_name=country["name"],
                country_code=country_code.upper(),
                hashtag=finale_hashtag,
                finale_message=finale_message,
                finale_task=finale_task,
                finale_details=finale_details,
                input_label=finale_input_label,
                finale_type=finale_type,
                error="Please enter your Buzz post URL.",
            )

        # For team_photo mode, accept any URL; for question mode, also accept any URL (since it's the final step)
        # Just validate it looks like a URL
        if not buzz_url.startswith(("http://", "https://")):
            return render_template(
                "finale.html",
                country_name=country["name"],
                country_code=country_code.upper(),
                hashtag=finale_hashtag,
                finale_message=finale_message,
                finale_task=finale_task,
                finale_details=finale_details,
                input_label=finale_input_label,
                finale_type=finale_type,
                error="Please enter a valid URL starting with http:// or https://",
            )

        now = datetime.utcnow()
        player_row = db.execute(
            "SELECT registered_at FROM players WHERE id=?", (player_id,)
        ).fetchone()

        total_seconds = 0
        if player_row:
            reg_dt = datetime.fromisoformat(player_row["registered_at"])
            total_seconds = (now - reg_dt).total_seconds()

        db.execute(
            "INSERT OR REPLACE INTO completions (player_id, buzz_post_url, completed_at, total_time_seconds) VALUES (?,?,?,?)",
            (player_id, buzz_url, now.isoformat(), total_seconds),
        )
        db.commit()

        # Calculate rank
        rank_row = db.execute(
            "SELECT COUNT(*) + 1 as r FROM completions WHERE player_id IN (SELECT id FROM players WHERE country_code=?) AND completed_at < ?",
            (country_code.upper(), now.isoformat()),
        ).fetchone()
        rank = rank_row["r"]

        return render_template(
            "complete.html",
            country_name=country["name"],
            country_code=country_code.upper(),
            rank=rank,
            total_time=format_duration(total_seconds),
            speed_quota=30,
        )

    return render_template(
        "finale.html",
        country_name=country["name"],
        country_code=country_code.upper(),
        hashtag=finale_hashtag,
        finale_message=finale_message,
        finale_task=finale_task,
        finale_details=finale_details,
        input_label=finale_input_label,
        finale_type=finale_type,
        error=None,
    )


@app.route("/complete/<country_code>")
def complete(country_code):
    country = get_country(country_code)
    if "player_id" not in session:
        return redirect(f"/register/{country_code}")

    player_id = session["player_id"]
    db = get_db()

    comp = db.execute(
        "SELECT * FROM completions WHERE player_id=?", (player_id,)
    ).fetchone()

    if not comp:
        return redirect(f"/finale/{country_code}")

    rank_row = db.execute(
        "SELECT COUNT(*) + 1 as r FROM completions WHERE player_id IN (SELECT id FROM players WHERE country_code=?) AND (total_time_seconds < ? OR (total_time_seconds = ? AND player_id < ?))",
        (country_code.upper(), comp["total_time_seconds"], comp["total_time_seconds"], player_id),
    ).fetchone()

    return render_template(
        "complete.html",
        country_name=country["name"],
        country_code=country_code.upper(),
        rank=rank_row["r"],
        total_time=format_duration(comp["total_time_seconds"]),
        speed_quota=30,
    )


@app.route("/leaderboard/<country_code>")
def leaderboard(country_code):
    country = get_country(country_code)
    db = get_db()

    players = db.execute("""
        SELECT p.name, p.employee_id, c.completed_at, c.total_time_seconds
        FROM completions c
        JOIN players p ON c.player_id = p.id
        WHERE p.country_code = ?
        ORDER BY c.total_time_seconds ASC
    """, (country_code.upper(),)).fetchall()

    player_list = []
    for p in players:
        player_list.append({
            "name": p["name"] or p["employee_id"],
            "total_time": format_duration(p["total_time_seconds"]),
        })

    return render_template(
        "leaderboard.html",
        country_name=country["name"],
        country_code=country_code.upper(),
        players=player_list,
        speed_quota=30,
    )


# ── Admin Routes ──────────────────────────────────────────────────────

@app.route("/admin/<country_code>/login", methods=["GET", "POST"])
def admin_login(country_code):
    country = get_country(country_code)
    error = None

    if request.method == "POST":
        pw = request.form.get("password", "")
        pw_hash = hashlib.sha256(pw.encode()).hexdigest()
        db = get_db()
        row = db.execute(
            "SELECT code FROM countries WHERE code=? AND admin_password_hash=?",
            (country_code.upper(), pw_hash),
        ).fetchone()
        if row:
            session[f"admin_{country_code}"] = country_code.upper()
            return redirect(f"/admin/{country_code}")
        error = "Incorrect password."

    return render_template(
        "admin_login.html",
        country_name=country["name"],
        country_code=country_code.upper(),
        error=error,
    )


@app.route("/admin/<country_code>/logout")
def admin_logout(country_code):
    session.pop(f"admin_{country_code}", None)
    return redirect(f"/admin/{country_code}/login")


@app.route("/admin/<country_code>")
@admin_required
def admin_dashboard(country_code):
    country = get_country(country_code)
    db = get_db()

    stops = db.execute(
        "SELECT * FROM stops WHERE country_code=? ORDER BY stop_number",
        (country_code.upper(),),
    ).fetchall()

    total_players = db.execute(
        "SELECT COUNT(*) as c FROM players WHERE country_code=?", (country_code.upper(),)
    ).fetchone()["c"]

    completed = db.execute("""
        SELECT COUNT(*) as c FROM completions c
        JOIN players p ON c.player_id = p.id
        WHERE p.country_code = ?
    """, (country_code.upper(),)).fetchone()["c"]

    lb = db.execute("""
        SELECT p.name, p.employee_id, c.completed_at, c.total_time_seconds
        FROM completions c
        JOIN players p ON c.player_id = p.id
        WHERE p.country_code = ?
        ORDER BY c.total_time_seconds ASC
    """, (country_code.upper(),)).fetchall()

    lb_list = []
    for p in lb:
        lb_list.append({
            "name": p["name"] or p["employee_id"],
            "employee_id": p["employee_id"],
            "completed_at": p["completed_at"],
            "total_time": format_duration(p["total_time_seconds"]),
        })

    return render_template(
        "admin_dashboard.html",
        country_name=country["name"],
        country_code=country_code.upper(),
        stops=stops,
        total_players=total_players,
        completed=completed,
        leaderboard=lb_list,
    )


@app.route("/admin/<country_code>/settings", methods=["GET", "POST"])
@admin_required
def admin_settings(country_code):
    country = get_country(country_code)
    db = get_db()
    error = None
    success = None

    if request.method == "POST":
        mechanics_text = request.form.get("mechanics_text", "").strip()
        first_clue = request.form.get("first_clue", "").strip()

        db.execute(
            "UPDATE countries SET mechanics_text=?, first_clue=? WHERE code=?",
            (mechanics_text, first_clue, country_code.upper()),
        )
        db.commit()
        success = "Settings saved successfully!"
        # Refresh country data
        country = get_country(country_code)

    return render_template(
        "admin_settings.html",
        country_name=country["name"],
        country_code=country_code.upper(),
        mechanics_text=country.get("mechanics_text", DEFAULT_MECHANICS),
        first_clue=country.get("first_clue", ""),
        error=error,
        success=success,
    )


@app.route("/admin/<country_code>/stop/add", methods=["GET", "POST"])
@admin_required
def admin_add_stop(country_code):
    country = get_country(country_code)
    error = None

    if request.method == "POST":
        location = request.form.get("location_name", "").strip()
        question = request.form.get("question", "").strip()
        answer = request.form.get("answer", "").strip()
        clue = request.form.get("clue_to_next", "").strip()
        is_finale = 1 if request.form.get("is_finale") else 0

        # Finale type: "question" or "team_photo"
        finale_type = request.form.get("finale_type", "team_photo").strip()
        if finale_type not in ("question", "team_photo"):
            finale_type = "team_photo"

        # Finale-specific fields
        finale_message = request.form.get("finale_message", "").strip()
        finale_task = request.form.get("finale_task", "").strip()
        finale_details = request.form.get("finale_details", "").strip()
        finale_hashtag = request.form.get("finale_hashtag", "").strip()
        finale_input_label = request.form.get("finale_input_label", "").strip()

        if not location:
            error = "Location name is required."
        else:
            db = get_db()
            max_num = db.execute(
                "SELECT COALESCE(MAX(stop_number),0) as m FROM stops WHERE country_code=?",
                (country_code.upper(),),
            ).fetchone()["m"]
            db.execute(
                "INSERT INTO stops (country_code, stop_number, location_name, question, answer, clue_to_next, is_finale, finale_type, finale_message, finale_task, finale_details, finale_hashtag, finale_input_label) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (country_code.upper(), max_num + 1, location, question, answer, clue, is_finale, finale_type, finale_message, finale_task, finale_details, finale_hashtag, finale_input_label),
            )
            db.commit()
            return redirect(f"/admin/{country_code}")

    return render_template(
        "admin_stop_form.html",
        country_name=country["name"],
        country_code=country_code.upper(),
        action_title="Add",
        form_action=f"/admin/{country_code}/stop/add",
        stop=None,
        error=error,
    )


@app.route("/admin/<country_code>/stop/<int:stop_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_stop(country_code, stop_id):
    country = get_country(country_code)
    db = get_db()
    error = None

    stop = db.execute("SELECT * FROM stops WHERE id=? AND country_code=?", (stop_id, country_code.upper())).fetchone()
    if not stop:
        abort(404)

    if request.method == "POST":
        location = request.form.get("location_name", "").strip()
        question = request.form.get("question", "").strip()
        answer = request.form.get("answer", "").strip()
        clue = request.form.get("clue_to_next", "").strip()
        is_finale = 1 if request.form.get("is_finale") else 0

        # Finale type: "question" or "team_photo"
        finale_type = request.form.get("finale_type", "team_photo").strip()
        if finale_type not in ("question", "team_photo"):
            finale_type = "team_photo"

        # Finale-specific fields
        finale_message = request.form.get("finale_message", "").strip()
        finale_task = request.form.get("finale_task", "").strip()
        finale_details = request.form.get("finale_details", "").strip()
        finale_hashtag = request.form.get("finale_hashtag", "").strip()
        finale_input_label = request.form.get("finale_input_label", "").strip()

        if not location:
            error = "Location name is required."
        else:
            db.execute(
                "UPDATE stops SET location_name=?, question=?, answer=?, clue_to_next=?, is_finale=?, finale_type=?, finale_message=?, finale_task=?, finale_details=?, finale_hashtag=?, finale_input_label=? WHERE id=?",
                (location, question, answer, clue, is_finale, finale_type, finale_message, finale_task, finale_details, finale_hashtag, finale_input_label, stop_id),
            )
            db.commit()
            return redirect(f"/admin/{country_code}")

    return render_template(
        "admin_stop_form.html",
        country_name=country["name"],
        country_code=country_code.upper(),
        action_title="Edit",
        form_action=f"/admin/{country_code}/stop/{stop_id}/edit",
        stop=stop,
        error=error,
    )


@app.route("/admin/<country_code>/stop/<int:stop_id>/delete")
@admin_required
def admin_delete_stop(country_code, stop_id):
    db = get_db()
    stop = db.execute("SELECT * FROM stops WHERE id=? AND country_code=?", (stop_id, country_code.upper())).fetchone()
    if stop:
        db.execute("DELETE FROM stops WHERE id=?", (stop_id,))
        # Renumber remaining stops
        remaining = db.execute(
            "SELECT id FROM stops WHERE country_code=? AND stop_number > ? ORDER BY stop_number",
            (country_code.upper(), stop["stop_number"]),
        ).fetchall()
        for i, r in enumerate(remaining, stop["stop_number"]):
            db.execute("UPDATE stops SET stop_number=? WHERE id=?", (i, r["id"]))
        db.commit()
    return redirect(f"/admin/{country_code}")


@app.route("/admin/<country_code>/reset")
@admin_required
def admin_reset(country_code):
    db = get_db()
    player_ids = [r["id"] for r in db.execute(
        "SELECT id FROM players WHERE country_code=?", (country_code.upper(),)
    ).fetchall()]
    for pid in player_ids:
        db.execute("DELETE FROM progress WHERE player_id=?", (pid,))
        db.execute("DELETE FROM completions WHERE player_id=?", (pid,))
    db.execute("DELETE FROM players WHERE country_code=?", (country_code.upper(),))
    db.commit()
    return redirect(f"/admin/{country_code}")



# ── API & Export ──────────────────────────────────────────────────────

@app.route("/api/leaderboard/<country_code>")
def api_leaderboard(country_code):
    country = get_country(country_code)
    db = get_db()

    players = db.execute("""
        SELECT p.name, p.employee_id, c.completed_at, c.total_time_seconds
        FROM completions c
        JOIN players p ON c.player_id = p.id
        WHERE p.country_code = ?
        ORDER BY c.total_time_seconds ASC
    """, (country_code.upper(),)).fetchall()

    result = []
    for i, p in enumerate(players, 1):
        result.append({
            "rank": i,
            "name": p["name"] or p["employee_id"],
            "employee_id": p["employee_id"],
            "completed_at": p["completed_at"],
            "total_time_seconds": p["total_time_seconds"],
            "total_time": format_duration(p["total_time_seconds"]),
        })

    return {"country": country["name"], "players": result}


@app.route("/export/<country_code>")
@admin_required
def export_csv(country_code):
    country = get_country(country_code)
    db = get_db()

    rows = db.execute("""
        SELECT p.employee_id, p.name, p.registered_at,
               c.completed_at, c.total_time_seconds, c.buzz_post_url
        FROM players p
        LEFT JOIN completions c ON p.id = c.player_id
        WHERE p.country_code = ?
        ORDER BY c.total_time_seconds ASC
    """, (country_code.upper(),)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee ID", "Name", "Registered At", "Completed At", "Total Time (s)", "Buzz URL"])
    for r in rows:
        writer.writerow([
            r["employee_id"],
            r["name"],
            r["registered_at"],
            r["completed_at"] or "",
            r["total_time_seconds"] or "",
            r["buzz_post_url"] or "",
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"hunt-{country_code}-export.csv",
    )


# ── Demo / Preview ────────────────────────────────────────────────────

@app.route("/demo")
def demo():
    """Preview all screens for Singapore without going through the flow."""
    screens = [
        {
            "title": "Homepage",
            "url": "/",
            "description": "Country selection page"
        },
        {
            "title": "Registration",
            "url": "/register/SG",
            "description": "Enter Employee ID with game mechanics"
        },
        {
            "title": "Clue 1 — First Clue + Answer",
            "url": "/clue/SG/1",
            "description": "First clue with answer input (go to location, read question, answer here)"
        },
        {
            "title": "Finale — Team Show Booth",
            "url": "/finale/SG",
            "description": "Team Show Booth - submit Buzz post URL"
        },
        {
            "title": "Complete",
            "url": "/demo/complete",
            "description": "Completion page with rank and prize"
        },
    ]
    return render_template("demo.html", screens=screens)


@app.route("/demo/complete")
def demo_complete():
    """Preview completion page for demo."""
    return render_template(
        "complete.html",
        country_name="Singapore",
        country_code="SG",
        rank=1,
        total_time="0m 15s",
        speed_quota=30
    )


# ─ Init & Run ────────────────────────────────────────────────────────

with app.app_context():
    init_db()
    migrate_db()
    seed_countries()
    seed_placeholder_stops()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
