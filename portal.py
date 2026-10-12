"""
Qararix Attendance Portal
Flask + SQLite. Run:  python app.py
"""
import csv
import io
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("QARARIX_DATA", os.path.join(BASE, "data"))
DB_PATH = os.path.join(DATA, "qararix.db")
try:
    os.makedirs(DATA, exist_ok=True)
except OSError:  # read-only filesystem (e.g. Vercel): fine when using PostgreSQL + SECRET_KEY
    pass

# Database: set DATABASE_URL (postgres://...) for hosted PostgreSQL, otherwise a local SQLite file is used.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
if IS_PG:
    import psycopg2
    import psycopg2.extras

app = Flask(__name__, static_folder=os.path.join(BASE, "public", "static"))  # public/ is served by the CDN on Vercel

# Persistent secret key (generated once, stored next to the database)
# Secret key: SECRET_KEY env var (recommended on cloud hosts), else a key file stored in the data folder
if os.environ.get("SECRET_KEY"):
    app.secret_key = os.environ["SECRET_KEY"]
else:
    _kf = os.path.join(DATA, "secret.key")
    try:
        if not os.path.exists(_kf):
            with open(_kf, "w") as f:
                f.write(secrets.token_hex(32))
        with open(_kf) as f:
            app.secret_key = f.read().strip()
    except OSError:
        raise RuntimeError("SECRET_KEY environment variable is not set (and the data folder is not writable). "
                           "Add SECRET_KEY in your host's Environment Variables and redeploy.")
if IS_PG or os.environ.get("TRUST_PROXY") == "1":  # running behind a host's HTTPS proxy
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config.update(
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("QARARIX_HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  emp_id TEXT UNIQUE NOT NULL,
  full_name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'employee',
  password_hash TEXT NOT NULL,
  must_change INTEGER NOT NULL DEFAULT 1,
  active INTEGER NOT NULL DEFAULT 1,
  designation TEXT DEFAULT '', department TEXT DEFAULT '',
  phone TEXT DEFAULT '', email TEXT DEFAULT '', cnic TEXT DEFAULT '',
  address TEXT DEFAULT '', dob TEXT DEFAULT '',
  emg_name TEXT DEFAULT '', emg_phone TEXT DEFAULT '',
  photo TEXT DEFAULT '', joined_on TEXT DEFAULT '',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS attendance(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  work_date TEXT NOT NULL,
  check_in TEXT, check_out TEXT,
  status TEXT NOT NULL DEFAULT 'Present',
  ip TEXT DEFAULT '', note TEXT DEFAULT '',
  UNIQUE(user_id, work_date)
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS photos(
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  mime TEXT NOT NULL, data BLOB NOT NULL
);
"""
if IS_PG:
    SCHEMA = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace("BLOB", "BYTEA")
DEFAULT_SETTINGS = {"office_start": "09:00", "grace_minutes": "15",
                    "timezone": "Asia/Karachi", "company": "Qararix"}


# ------------------------------------------------------------------ database
class _PG:
    """Tiny wrapper so the same SQL ('?' placeholders) runs on PostgreSQL and SQLite."""
    def __init__(self):
        self.conn = psycopg2.connect(DATABASE_URL, connect_timeout=10,
                                     cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def connect():
    if IS_PG:
        return _PG()
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def blob(b):
    return psycopg2.Binary(b) if IS_PG else sqlite3.Binary(b)


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    con = connect()
    if IS_PG:
        con.execute(SCHEMA)
    else:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO NOTHING", (k, v))
    con.execute("UPDATE users SET photo='' WHERE photo<>'' AND id NOT IN (SELECT user_id FROM photos)")
    if not con.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        pw = os.environ.get("QARARIX_ADMIN_PASSWORD") or (gen_password() if IS_PG else "Qararix@123")
        con.execute(
            "INSERT INTO users(emp_id,full_name,role,password_hash,must_change,designation)"
            " VALUES('ADMIN','Administrator','admin',?,1,'System Administrator') ON CONFLICT(emp_id) DO NOTHING",
            (generate_password_hash(pw),))
        print("\n  First run: admin login -> ID: ADMIN   Password: %s  (you must change it on first login)\n" % pw, flush=True)
    con.commit()
    con.close()


def setting(key):
    r = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else DEFAULT_SETTINGS.get(key, "")


def now():
    try:
        return datetime.now(ZoneInfo(setting("timezone")))
    except Exception:
        return datetime.now()


# ------------------------------------------------------------------ helpers
PROFILE_FIELDS = [
    ("photo", "Profile photo", 15, lambda u: bool(u["photo"])),
    ("phone", "Phone number", 15, lambda u: bool(u["phone"])),
    ("email", "Email address", 15, lambda u: bool(u["email"])),
    ("cnic", "CNIC", 15, lambda u: bool(u["cnic"])),
    ("address", "Home address", 15, lambda u: bool(u["address"])),
    ("dob", "Date of birth", 10, lambda u: bool(u["dob"])),
    ("emg", "Emergency contact", 15, lambda u: bool(u["emg_name"] and u["emg_phone"])),
]


def completion(u):
    items = [(label, w, fn(u)) for _, label, w, fn in PROFILE_FIELDS]
    return {"score": sum(w for _, w, ok in items if ok), "parts": items,
            "missing": [l for l, _, ok in items if not ok]}


def hours(row):
    try:
        a = datetime.strptime(row["check_in"], "%H:%M:%S")
        b = datetime.strptime(row["check_out"], "%H:%M:%S")
        m = int((b - a).total_seconds() // 60)
        return "%dh %02dm" % (m // 60, m % 60) if m >= 0 else ""
    except Exception:
        return ""


def t12(s):
    try:
        return datetime.strptime(s, "%H:%M:%S").strftime("%I:%M %p").lstrip("0")
    except Exception:
        return "—"


def d_nice(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%a, %d %b %Y")
    except Exception:
        return s or "—"


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals.update(initials=lambda n: "".join(w[0] for w in n.split()[:2]).upper(), csrf_token=csrf_token, completion=completion,
                             hours=hours, t12=t12, d_nice=d_nice)


@app.before_request
def guard():
    g.user = None
    if request.endpoint == "static":
        return
    if request.method == "POST":
        sent = request.form.get("_csrf", "").encode()
        if not secrets.compare_digest(sent, session.get("_csrf", "x").encode()):
            abort(400, "Your session expired. Go back, refresh the page and try again.")
    uid = session.get("uid")
    if uid:
        u = db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
        if u:
            g.user = u
        else:
            session.clear()
    if (g.user and g.user["must_change"] and
            request.endpoint not in ("change_password", "logout", "static")):
        return redirect(url_for("change_password"))


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user:
            return redirect(url_for("login"))
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not g.user:
            return redirect(url_for("login"))
        if g.user["role"] != "admin":
            abort(403)
        return f(*a, **k)
    return w


def gen_password(n=10):
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n)) + "#"


def next_emp_id():
    nums = [int(m.group(1)) for r in db().execute("SELECT emp_id FROM users")
            if (m := re.fullmatch(r"QRX-(\d+)", r["emp_id"]))]
    return "QRX-%03d" % (max(nums, default=0) + 1)


def client_ip():
    return request.remote_addr or ""


# ------------------------------------------------------------------ auth
_fails = {}


@app.route("/")
def index():
    if not g.user:
        return redirect(url_for("login"))
    return redirect(url_for("admin_home" if g.user["role"] == "admin" else "dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        emp = request.form.get("emp_id", "").strip().upper()
        key = (client_ip(), emp)
        recent = [t for t in _fails.get(key, []) if time.time() - t < 600]
        _fails[key] = recent
        if len(recent) >= 5:
            flash("Too many failed attempts. Please wait 10 minutes and try again.", "error")
            return render_template("login.html"), 429
        u = db().execute("SELECT * FROM users WHERE emp_id=? AND active=1", (emp,)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            _fails.pop(key, None)
            session.clear()
            session.permanent = True
            session["uid"] = u["id"]
            csrf_token()
            return redirect(url_for("index"))
        _fails[key].append(time.time())
        flash("Employee ID or password is incorrect.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        cur, new, new2 = (request.form.get(k, "") for k in ("current", "new", "confirm"))
        if not check_password_hash(g.user["password_hash"], cur):
            flash("Your current password is incorrect.", "error")
        elif len(new) < 8 or not re.search(r"[A-Za-z]", new) or not re.search(r"\d", new):
            flash("New password must be at least 8 characters and include a letter and a number.", "error")
        elif new != new2:
            flash("The two new passwords do not match.", "error")
        elif new == cur:
            flash("Choose a password different from the current one.", "error")
        else:
            db().execute("UPDATE users SET password_hash=?, must_change=0 WHERE id=?",
                         (generate_password_hash(new), g.user["id"]))
            db().commit()
            flash("Password updated.", "ok")
            return redirect(url_for("index"))
    return render_template("password.html", forced=bool(g.user["must_change"]))


# ------------------------------------------------------------------ employee
def today_record(uid):
    return db().execute("SELECT * FROM attendance WHERE user_id=? AND work_date=?",
                        (uid, now().strftime("%Y-%m-%d"))).fetchone()


@app.route("/dashboard")
@login_required
def dashboard():
    if g.user["role"] == "admin":
        return redirect(url_for("admin_home"))
    n = now()
    rows = db().execute("SELECT * FROM attendance WHERE user_id=? ORDER BY work_date DESC LIMIT 31",
                        (g.user["id"],)).fetchall()
    month = n.strftime("%Y-%m")
    stats = db().execute(
        "SELECT SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) p, SUM(CASE WHEN status='Late' THEN 1 ELSE 0 END) l FROM attendance "
        "WHERE user_id=? AND work_date LIKE ?", (g.user["id"], month + "%")).fetchone()
    return render_template("dashboard.html", rec=today_record(g.user["id"]), rows=rows, now=n,
                           stats=stats, tzname=setting("timezone"),
                           office_start=setting("office_start"))


@app.route("/checkin", methods=["POST"])
@login_required
def checkin():
    n = now()
    if today_record(g.user["id"]):
        flash("You have already checked in today.", "error")
        return redirect(url_for("dashboard"))
    h, m = map(int, setting("office_start").split(":"))
    limit = n.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(minutes=int(setting("grace_minutes")))
    status = "Late" if n > limit else "Present"
    db().execute("INSERT INTO attendance(user_id,work_date,check_in,status,ip) VALUES(?,?,?,?,?)",
                 (g.user["id"], n.strftime("%Y-%m-%d"), n.strftime("%H:%M:%S"), status, client_ip()))
    db().commit()
    flash("Checked in at %s%s." % (t12(n.strftime("%H:%M:%S")), " (marked late)" if status == "Late" else ""), "ok")
    return redirect(url_for("dashboard"))


@app.route("/checkout", methods=["POST"])
@login_required
def checkout():
    rec = today_record(g.user["id"])
    if not rec:
        flash("Check in first before checking out.", "error")
    elif rec["check_out"]:
        flash("You have already checked out today.", "error")
    else:
        t = now().strftime("%H:%M:%S")
        db().execute("UPDATE attendance SET check_out=? WHERE id=?", (t, rec["id"]))
        db().commit()
        flash("Checked out at %s. See you tomorrow." % t12(t), "ok")
    return redirect(url_for("dashboard"))


def valid_image(head):
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    u = g.user
    if request.method == "POST":
        f = request.form
        phone, email = f.get("phone", "").strip(), f.get("email", "").strip()
        cnic = re.sub(r"\D", "", f.get("cnic", ""))
        errs = []
        if phone and not re.fullmatch(r"\+?[0-9\- ]{10,16}", phone):
            errs.append("Phone number looks invalid (example: 0300-1234567).")
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            errs.append("Email address looks invalid.")
        if cnic and len(cnic) != 13:
            errs.append("CNIC must have 13 digits (example: 12345-1234567-1).")
        if len(cnic) == 13:
            cnic = "%s-%s-%s" % (cnic[:5], cnic[5:12], cnic[12])
        dob = f.get("dob", "").strip()
        if dob:
            try:
                datetime.strptime(dob, "%Y-%m-%d")
            except ValueError:
                errs.append("Date of birth is invalid.")
        ephone = f.get("emg_phone", "").strip()
        if ephone and not re.fullmatch(r"\+?[0-9\- ]{10,16}", ephone):
            errs.append("Emergency contact phone looks invalid.")
        photo = u["photo"]
        new_photo = None
        up = request.files.get("photo")
        if up and up.filename:
            data = up.read()
            ext = valid_image(data[:16])
            if not ext:
                errs.append("Photo must be a JPG, PNG or WEBP image (max 3 MB).")
            elif not errs:
                photo = secrets.token_hex(6)  # version tag, busts the browser cache
                new_photo = ({"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[ext], data)
        if errs:
            for e in errs:
                flash(e, "error")
        else:
            if new_photo:
                db().execute("INSERT INTO photos(user_id,mime,data) VALUES(?,?,?) ON CONFLICT(user_id) "
                             "DO UPDATE SET mime=excluded.mime, data=excluded.data",
                             (u["id"], new_photo[0], blob(new_photo[1])))
            db().execute(
                "UPDATE users SET phone=?,email=?,cnic=?,address=?,dob=?,emg_name=?,emg_phone=?,photo=? WHERE id=?",
                (phone, email, cnic, f.get("address", "").strip()[:250], dob,
                 f.get("emg_name", "").strip()[:80], ephone, photo, u["id"]))
            db().commit()
            flash("Profile saved.", "ok")
            return redirect(url_for("profile"))
        u = dict(u)
        u.update(phone=phone, email=email, cnic=f.get("cnic", ""), address=f.get("address", ""), dob=dob,
                 emg_name=f.get("emg_name", ""), emg_phone=ephone)
    return render_template("profile.html", u=u)


@app.route("/avatar/<int:uid>")
@login_required
def avatar(uid):
    if g.user["role"] != "admin" and g.user["id"] != uid:
        abort(403)
    r = db().execute("SELECT mime,data FROM photos WHERE user_id=?", (uid,)).fetchone()
    if not r:
        abort(404)
    return Response(bytes(r["data"]), mimetype=r["mime"], headers={"Cache-Control": "private, max-age=86400"})


# ------------------------------------------------------------------ admin
@app.route("/admin")
@admin_required
def admin_home():
    today = now().strftime("%Y-%m-%d")
    present = db().execute(
        "SELECT a.*, u.full_name, u.emp_id, u.photo, u.designation FROM attendance a "
        "JOIN users u ON u.id=a.user_id WHERE a.work_date=? ORDER BY a.check_in DESC", (today,)).fetchall()
    absent = db().execute(
        "SELECT * FROM users WHERE role='employee' AND active=1 AND id NOT IN "
        "(SELECT user_id FROM attendance WHERE work_date=?) ORDER BY full_name", (today,)).fetchall()
    total = len(present) + len(absent)
    late = sum(1 for r in present if r["status"] == "Late")
    return render_template("admin_home.html", present=present, absent=absent, total=total,
                           late=late, today=today, now=now())


def attendance_query():
    n = now()
    a = request.args
    start = a.get("from") or n.strftime("%Y-%m-01")
    end = a.get("to") or n.strftime("%Y-%m-%d")
    q = ("SELECT a.*, u.full_name, u.emp_id, u.department FROM attendance a JOIN users u ON u.id=a.user_id "
         "WHERE a.work_date BETWEEN ? AND ?")
    p = [start, end]
    if a.get("emp"):
        q += " AND u.id=?"
        p.append(a["emp"])
    if a.get("status"):
        q += " AND a.status=?"
        p.append(a["status"])
    if a.get("q"):
        q += " AND (LOWER(u.full_name) LIKE ? OR LOWER(u.emp_id) LIKE ?)"
        p += ["%" + a["q"].lower() + "%"] * 2
    q += " ORDER BY a.work_date DESC, a.check_in DESC"
    return db().execute(q, p).fetchall(), start, end


@app.route("/admin/attendance")
@admin_required
def admin_attendance():
    rows, start, end = attendance_query()
    emps = db().execute("SELECT id,emp_id,full_name FROM users WHERE role='employee' ORDER BY full_name").fetchall()
    return render_template("admin_attendance.html", rows=rows, start=start, end=end, emps=emps, a=request.args)


@app.route("/admin/attendance.csv")
@admin_required
def admin_attendance_csv():
    rows, start, end = attendance_query()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Employee ID", "Name", "Department", "Date", "Check-in", "Check-out", "Hours", "Status", "IP"])
    for r in rows:
        w.writerow([r["emp_id"], r["full_name"], r["department"], r["work_date"], r["check_in"] or "",
                    r["check_out"] or "", hours(r), r["status"], r["ip"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=qararix_attendance_%s_to_%s.csv" % (start, end)})


@app.route("/admin/attendance/edit", methods=["GET", "POST"])
@admin_required
def admin_attendance_edit():
    f = request.values
    emps = db().execute("SELECT id,emp_id,full_name FROM users WHERE role='employee' AND active=1 ORDER BY full_name").fetchall()
    uid, day = f.get("user_id", ""), f.get("date", now().strftime("%Y-%m-%d"))
    rec = db().execute("SELECT * FROM attendance WHERE user_id=? AND work_date=?", (uid, day)).fetchone() if uid else None
    if request.method == "POST":
        def tm(v):
            v = v.strip()
            if not v:
                return None
            return v if len(v) == 8 else v + ":00"
        ci, co = tm(f.get("check_in", "")), tm(f.get("check_out", ""))
        st = f.get("status", "Present")
        if not uid or st not in ("Present", "Late", "Absent", "Leave"):
            flash("Choose an employee and a valid status.", "error")
        else:
            db().execute(
                "INSERT INTO attendance(user_id,work_date,check_in,check_out,status,note,ip) VALUES(?,?,?,?,?,?, 'admin') "
                "ON CONFLICT(user_id,work_date) DO UPDATE SET check_in=excluded.check_in, "
                "check_out=excluded.check_out, status=excluded.status, note=excluded.note",
                (uid, day, ci, co, st, f.get("note", "")[:200]))
            db().commit()
            flash("Attendance saved.", "ok")
            return redirect(url_for("admin_attendance", **{"from": day, "to": day}))
    return render_template("admin_attendance_edit.html", emps=emps, uid=uid, day=day, rec=rec)


@app.route("/admin/employees")
@admin_required
def admin_employees():
    rows = db().execute("SELECT * FROM users WHERE role='employee' ORDER BY active DESC, full_name").fetchall()
    return render_template("admin_employees.html", rows=rows)


def read_employee_form(f):
    return dict(emp_id=f.get("emp_id", "").strip().upper(), full_name=f.get("full_name", "").strip(),
                designation=f.get("designation", "").strip(), department=f.get("department", "").strip(),
                joined_on=f.get("joined_on", "").strip())


@app.route("/admin/employees/new", methods=["GET", "POST"])
@admin_required
def admin_employee_new():
    if request.method == "POST":
        d = read_employee_form(request.form)
        pw = request.form.get("password", "").strip() or gen_password()
        if not re.fullmatch(r"[A-Z0-9_-]{3,20}", d["emp_id"]):
            flash("Employee ID must be 3–20 letters, numbers, dash or underscore.", "error")
        elif not d["full_name"]:
            flash("Full name is required.", "error")
        elif len(pw) < 8:
            flash("Temporary password must be at least 8 characters.", "error")
        elif db().execute("SELECT 1 FROM users WHERE emp_id=?", (d["emp_id"],)).fetchone():
            flash("That Employee ID is already taken.", "error")
        else:
            db().execute(
                "INSERT INTO users(emp_id,full_name,designation,department,joined_on,password_hash,must_change) VALUES(?,?,?,?,?,?,1)",
                (d["emp_id"], d["full_name"], d["designation"], d["department"], d["joined_on"], generate_password_hash(pw)))
            db().commit()
            flash("Employee created. Share these login details — ID: %s  Password: %s  (they must change it at first login)" % (d["emp_id"], pw), "cred")
            return redirect(url_for("admin_employees"))
    return render_template("admin_employee_form.html", e=None, suggest=next_emp_id(), tmp=gen_password())


@app.route("/admin/employees/<int:uid>", methods=["GET", "POST"])
@admin_required
def admin_employee_edit(uid):
    e = db().execute("SELECT * FROM users WHERE id=? AND role='employee'", (uid,)).fetchone()
    if not e:
        abort(404)
    if request.method == "POST":
        d = read_employee_form(request.form)
        clash = db().execute("SELECT 1 FROM users WHERE emp_id=? AND id<>?", (d["emp_id"], uid)).fetchone()
        if not re.fullmatch(r"[A-Z0-9_-]{3,20}", d["emp_id"]) or not d["full_name"]:
            flash("Enter a valid Employee ID and full name.", "error")
        elif clash:
            flash("That Employee ID is already taken.", "error")
        else:
            db().execute("UPDATE users SET emp_id=?,full_name=?,designation=?,department=?,joined_on=? WHERE id=?",
                         (d["emp_id"], d["full_name"], d["designation"], d["department"], d["joined_on"], uid))
            db().commit()
            flash("Employee updated.", "ok")
            return redirect(url_for("admin_employees"))
    month = now().strftime("%Y-%m")
    st = db().execute("SELECT SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) p, SUM(CASE WHEN status='Late' THEN 1 ELSE 0 END) l, COUNT(*) n FROM attendance "
                      "WHERE user_id=? AND work_date LIKE ?", (uid, month + "%")).fetchone()
    return render_template("admin_employee_form.html", e=e, suggest=e["emp_id"], tmp="", st=st)


@app.route("/admin/employees/<int:uid>/reset", methods=["POST"])
@admin_required
def admin_employee_reset(uid):
    e = db().execute("SELECT * FROM users WHERE id=? AND role='employee'", (uid,)).fetchone()
    if not e:
        abort(404)
    pw = gen_password()
    db().execute("UPDATE users SET password_hash=?, must_change=1 WHERE id=?", (generate_password_hash(pw), uid))
    db().commit()
    flash("New temporary password for %s — ID: %s  Password: %s" % (e["full_name"], e["emp_id"], pw), "cred")
    return redirect(url_for("admin_employees"))


@app.route("/admin/employees/<int:uid>/toggle", methods=["POST"])
@admin_required
def admin_employee_toggle(uid):
    e = db().execute("SELECT * FROM users WHERE id=? AND role='employee'", (uid,)).fetchone()
    if not e:
        abort(404)
    db().execute("UPDATE users SET active=? WHERE id=?", (0 if e["active"] else 1, uid))
    db().commit()
    flash("%s %s." % (e["full_name"], "deactivated" if e["active"] else "re-activated"), "ok")
    return redirect(url_for("admin_employees"))


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        f = request.form
        try:
            datetime.strptime(f["office_start"], "%H:%M")
            int(f["grace_minutes"])
            ZoneInfo(f["timezone"])
        except Exception:
            flash("Please enter a valid start time, grace minutes and timezone (e.g. Asia/Karachi).", "error")
        else:
            for k in ("office_start", "grace_minutes", "timezone"):
                db().execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, f[k].strip()))
            db().commit()
            flash("Settings saved.", "ok")
    return render_template("admin_settings.html", s={k: setting(k) for k in DEFAULT_SETTINGS})


@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(400)
@app.errorhandler(413)
def err(e):
    code = getattr(e, "code", 500)
    msg = {403: "You don't have access to this page.", 404: "We couldn't find that page.",
           413: "That file is too large (max 3 MB)."}.get(code, getattr(e, "description", "Something went wrong."))
    return render_template("error.html", code=code, msg=msg), code


try:
    init_db()
except Exception as exc:  # e.g. two cold starts racing to create tables; the next request retries nothing harmful
    print("init_db warning:", exc, flush=True)

@app.errorhandler(Exception)
def crash(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    import traceback
    tb = traceback.format_exc()
    print(tb, flush=True)
    detail = tb if os.environ.get("QARARIX_DEBUG") == "1" else ""
    return render_template("error.html", code=500, msg="Something went wrong on our side.", detail=detail), 500


def main():
    host = os.environ.get("QARARIX_HOST", "0.0.0.0")  # 0.0.0.0 = reachable from other PCs on your Wi-Fi/LAN
    port = int(os.environ.get("PORT") or os.environ.get("QARARIX_PORT", "5000"))
    try:
        from waitress import serve
        print("Qararix Portal running on http://%s:%d" % ("localhost", port))
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port)
