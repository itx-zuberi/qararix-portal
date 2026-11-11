"""
Qararix Mobile REST API  (v1)
Adds a JSON API to the existing Qararix Attendance Portal so the Android app can sync with it.
It uses the SAME database, the SAME users and the SAME rules as the website.

Auth: POST /api/v1/auth/login  ->  token.  Then send   Authorization: Bearer <token>
Tokens are signed with the portal SECRET_KEY, last 30 days and stop working the moment the
password is changed or the user is deactivated.
"""
import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, Response, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint("api", __name__, url_prefix="/api/v1")

P = None            # the portal module, injected by init() (avoids a circular import)
API_VERSION = 1
TOKEN_MAX_AGE = 30 * 24 * 3600

EXPENSE_CATEGORIES = ["Salaries", "Rent", "Utilities", "Internet", "Office Supplies", "Travel", "Food",
                      "Marketing", "Equipment", "Software", "Maintenance", "Taxes", "Other"]
INCOME_CATEGORIES = ["Client Payment", "Project", "Service", "Investment", "Other"]
PAY_METHODS = ["Cash", "Bank", "Card", "Online", "Other"]
STATUSES = ("Present", "Late", "Absent", "Leave")


def init(portal_module):
    global P
    P = portal_module


# ------------------------------------------------------------------ errors / helpers
class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def ok(**data):
    data["ok"] = True
    return jsonify(data)


def _err(status, code, message):
    resp = jsonify({"ok": False, "error": {"code": code, "message": message}})
    resp.status_code = status
    return resp


@bp.errorhandler(ApiError)
def _on_api_error(e):
    return _err(e.status, e.code, e.message)


@bp.errorhandler(HTTPException)
def _on_http_error(e):
    msg = {413: "That file is too large (max 3 MB)."}.get(e.code, e.description or "Request failed.")
    return _err(e.code or 500, "http_%s" % e.code, msg)


@bp.errorhandler(Exception)
def _on_crash(e):
    import traceback
    print(traceback.format_exc(), flush=True)
    return _err(500, "server_error", "Something went wrong on the server.")


def body():
    return request.get_json(silent=True) or {}


def s(v, n=250):
    return str(v if v is not None else "").strip()[:n]


def fp(u):
    """Fingerprint of the password hash: changing the password invalidates every old token."""
    return hashlib.sha256((u["password_hash"] or "").encode()).hexdigest()[:12]


def _ser():
    return URLSafeTimedSerializer(P.app.secret_key, salt="qararix-mobile-v1")


def make_token(u):
    return _ser().dumps({"u": u["id"], "f": fp(u)})


def api_auth(admin=False, allow_must_change=False):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            h = request.headers.get("Authorization", "")
            if not h.startswith("Bearer "):
                raise ApiError(401, "unauthorized", "Please sign in.")
            try:
                data = _ser().loads(h[7:].strip(), max_age=TOKEN_MAX_AGE)
            except SignatureExpired:
                raise ApiError(401, "expired", "Your session expired. Please sign in again.")
            except BadSignature:
                raise ApiError(401, "unauthorized", "Please sign in.")
            u = P.db().execute("SELECT * FROM users WHERE id=? AND active=1", (data.get("u"),)).fetchone()
            if not u or data.get("f") != fp(u):
                raise ApiError(401, "unauthorized", "Please sign in again.")
            if u["must_change"] and not allow_must_change:
                raise ApiError(403, "must_change", "You must change your temporary password first.")
            if admin and u["role"] != "admin":
                raise ApiError(403, "forbidden", "Administrator access only.")
            g.me = u
            return f(*a, **k)
        return w
    return deco


def user_json(u, full=True):
    d = {"id": u["id"], "emp_id": u["emp_id"], "full_name": u["full_name"], "role": u["role"],
         "active": bool(u["active"]), "designation": u["designation"] or "", "department": u["department"] or "",
         "joined_on": u["joined_on"] or "", "has_photo": bool(u["photo"]), "photo_v": u["photo"] or ""}
    if full:
        d.update(phone=u["phone"] or "", email=u["email"] or "", cnic=u["cnic"] or "", address=u["address"] or "",
                 dob=u["dob"] or "", emg_name=u["emg_name"] or "", emg_phone=u["emg_phone"] or "",
                 must_change=bool(u["must_change"]))
        c = P.completion(u)
        d["completion"] = {"score": c["score"], "missing": c["missing"]}
    return d


def att_json(r, with_person=False):
    d = {"id": r["id"], "date": r["work_date"], "check_in": r["check_in"] or "", "check_out": r["check_out"] or "",
         "check_in_label": P.t12(r["check_in"]) if r["check_in"] else "", "check_out_label": P.t12(r["check_out"]) if r["check_out"] else "",
         "hours": P.hours(r) if (r["check_in"] and r["check_out"]) else "", "status": r["status"], "note": r["note"] or ""}
    if with_person:
        d.update(user_id=r["user_id"], emp_id=r["emp_id"], full_name=r["full_name"],
                 department=(r["department"] if "department" in r.keys() else "") or "")
    return d


def valid_date(v, field="Date"):
    try:
        datetime.strptime(v, "%Y-%m-%d")
        return v
    except (ValueError, TypeError):
        raise ApiError(400, "bad_date", "%s must look like 2026-11-30." % field)


def clock_info():
    n = P.now()
    return {"server_date": n.strftime("%Y-%m-%d"), "server_time": n.strftime("%H:%M:%S"),
            "server_iso": n.isoformat(), "timezone": P.setting("timezone")}


# ------------------------------------------------------------------ public
@bp.get("/config")
def config():
    return ok(app="Qararix Portal", api_version=API_VERSION, company=P.setting("company"), **clock_info())


@bp.post("/auth/login")
def login():
    d = body()
    emp = s(d.get("emp_id"), 40).upper()
    pw = d.get("password") or ""
    key = (P.client_ip(), emp)
    recent = [t for t in P._fails.get(key, []) if time.time() - t < 600]
    P._fails[key] = recent
    if len(recent) >= 5:
        raise ApiError(429, "too_many", "Too many failed attempts. Please wait 10 minutes and try again.")
    u = P.db().execute("SELECT * FROM users WHERE emp_id=? AND active=1", (emp,)).fetchone()
    if not u or not check_password_hash(u["password_hash"], pw):
        P._fails[key].append(time.time())
        raise ApiError(401, "bad_credentials", "Employee ID or password is incorrect.")
    P._fails.pop(key, None)
    return ok(token=make_token(u), must_change=bool(u["must_change"]), user=user_json(u),
              company=P.setting("company"), **clock_info())


@bp.post("/auth/change-password")
@api_auth(allow_must_change=True)
def change_password():
    d = body()
    cur, new, new2 = d.get("current") or "", d.get("new") or "", d.get("confirm") or ""
    u = g.me
    if not check_password_hash(u["password_hash"], cur):
        raise ApiError(400, "bad_current", "Your current password is incorrect.")
    if len(new) < 8 or not re.search(r"[A-Za-z]", new) or not re.search(r"\d", new):
        raise ApiError(400, "weak_password", "New password must be at least 8 characters and include a letter and a number.")
    if new != new2:
        raise ApiError(400, "mismatch", "The two new passwords do not match.")
    if new == cur:
        raise ApiError(400, "same_password", "Choose a password different from the current one.")
    P.db().execute("UPDATE users SET password_hash=?, must_change=0 WHERE id=?", (generate_password_hash(new), u["id"]))
    P.db().commit()
    fresh = P.db().execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    return ok(token=make_token(fresh), user=user_json(fresh), message="Password updated.")


# ------------------------------------------------------------------ me / profile
@bp.get("/me")
@api_auth(allow_must_change=True)
def me():
    return ok(user=user_json(g.me), company=P.setting("company"), **clock_info())


@bp.put("/me")
@api_auth()
def update_me():
    u, d = g.me, body()
    phone, email = s(d.get("phone"), 20), s(d.get("email"), 120)
    cnic = re.sub(r"\D", "", s(d.get("cnic"), 30))
    dob, ephone = s(d.get("dob"), 10), s(d.get("emg_phone"), 20)
    if phone and not re.fullmatch(r"\+?[0-9\- ]{10,16}", phone):
        raise ApiError(400, "bad_phone", "Phone number looks invalid (example: 0300-1234567).")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ApiError(400, "bad_email", "Email address looks invalid.")
    if cnic and len(cnic) != 13:
        raise ApiError(400, "bad_cnic", "CNIC must have 13 digits (example: 12345-1234567-1).")
    if len(cnic) == 13:
        cnic = "%s-%s-%s" % (cnic[:5], cnic[5:12], cnic[12])
    if dob:
        valid_date(dob, "Date of birth")
    if ephone and not re.fullmatch(r"\+?[0-9\- ]{10,16}", ephone):
        raise ApiError(400, "bad_phone", "Emergency contact phone looks invalid.")
    name, desig = u["full_name"], u["designation"]
    if u["role"] == "admin":   # the administrator may edit own name / title; employees cannot
        name = s(d.get("full_name"), 80) or name
        desig = s(d.get("designation"), 80) if d.get("designation") is not None else desig
    P.db().execute(
        "UPDATE users SET full_name=?,designation=?,phone=?,email=?,cnic=?,address=?,dob=?,emg_name=?,emg_phone=? WHERE id=?",
        (name, desig, phone, email, cnic, s(d.get("address"), 250), dob, s(d.get("emg_name"), 80), ephone, u["id"]))
    P.db().commit()
    fresh = P.db().execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
    return ok(user=user_json(fresh), message="Profile saved.")


@bp.post("/me/photo")
@api_auth()
def upload_photo():
    up = request.files.get("photo")
    if not up:
        raise ApiError(400, "no_file", "No photo was sent.")
    data = up.read()
    ext = P.valid_image(data[:16])
    if not ext:
        raise ApiError(400, "bad_image", "Photo must be a JPG, PNG or WEBP image (max 3 MB).")
    mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[ext]
    tag = secrets.token_hex(6)
    P.db().execute("INSERT INTO photos(user_id,mime,data) VALUES(?,?,?) ON CONFLICT(user_id) "
                   "DO UPDATE SET mime=excluded.mime, data=excluded.data", (g.me["id"], mime, P.blob(data)))
    P.db().execute("UPDATE users SET photo=? WHERE id=?", (tag, g.me["id"]))
    P.db().commit()
    fresh = P.db().execute("SELECT * FROM users WHERE id=?", (g.me["id"],)).fetchone()
    return ok(user=user_json(fresh), message="Photo updated.")


@bp.delete("/me/photo")
@api_auth()
def delete_photo():
    P.db().execute("DELETE FROM photos WHERE user_id=?", (g.me["id"],))
    P.db().execute("UPDATE users SET photo='' WHERE id=?", (g.me["id"],))
    P.db().commit()
    fresh = P.db().execute("SELECT * FROM users WHERE id=?", (g.me["id"],)).fetchone()
    return ok(user=user_json(fresh), message="Photo removed.")


@bp.get("/avatar/<int:uid>")
@api_auth()
def avatar(uid):
    if g.me["role"] != "admin" and g.me["id"] != uid:
        raise ApiError(403, "forbidden", "You don't have access to this photo.")
    r = P.db().execute("SELECT mime,data FROM photos WHERE user_id=?", (uid,)).fetchone()
    if not r:
        raise ApiError(404, "no_photo", "No photo.")
    return Response(bytes(r["data"]), mimetype=r["mime"], headers={"Cache-Control": "private, max-age=31536000, immutable"})


# ------------------------------------------------------------------ employee: attendance
def _today_rec(uid):
    return P.db().execute("SELECT * FROM attendance WHERE user_id=? AND work_date=?",
                          (uid, P.now().strftime("%Y-%m-%d"))).fetchone()


def _month_stats(uid, month):
    r = P.db().execute(
        "SELECT SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) p, SUM(CASE WHEN status='Late' THEN 1 ELSE 0 END) l, "
        "SUM(CASE WHEN status='Absent' THEN 1 ELSE 0 END) a, SUM(CASE WHEN status='Leave' THEN 1 ELSE 0 END) v "
        "FROM attendance WHERE user_id=? AND work_date LIKE ?", (uid, month + "%")).fetchone()
    return {"present": int(r["p"] or 0), "late": int(r["l"] or 0), "absent": int(r["a"] or 0), "leave": int(r["v"] or 0)}


@bp.get("/dashboard")
@api_auth()
def dashboard():
    n = P.now()
    uid = g.me["id"]
    rows = P.db().execute("SELECT * FROM attendance WHERE user_id=? ORDER BY work_date DESC LIMIT 31", (uid,)).fetchall()
    today = n.strftime("%Y-%m-%d")
    rec = next((r for r in rows if r["work_date"] == today), None)
    return ok(user=user_json(g.me), today=att_json(rec) if rec else None, stats=_month_stats(uid, n.strftime("%Y-%m")),
              history=[att_json(r) for r in rows], office_start=P.setting("office_start"),
              grace_minutes=int(P.setting("grace_minutes")), company=P.setting("company"), **clock_info())


@bp.post("/checkin")
@api_auth()
def checkin():
    n = P.now()
    if _today_rec(g.me["id"]):
        raise ApiError(409, "already_checked_in", "You have already checked in today.")
    h, m = map(int, P.setting("office_start").split(":"))
    limit = n.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(minutes=int(P.setting("grace_minutes")))
    status = "Late" if n > limit else "Present"
    try:
        P.db().execute("INSERT INTO attendance(user_id,work_date,check_in,status,ip) VALUES(?,?,?,?,?)",
                       (g.me["id"], n.strftime("%Y-%m-%d"), n.strftime("%H:%M:%S"), status, (P.client_ip() or "") + " (app)"))
        P.db().commit()
    except Exception:
        raise ApiError(409, "already_checked_in", "You have already checked in today.")
    rec = _today_rec(g.me["id"])
    return ok(today=att_json(rec), message="Checked in at %s%s." % (P.t12(n.strftime("%H:%M:%S")), " (marked late)" if status == "Late" else ""))


@bp.post("/checkout")
@api_auth()
def checkout():
    rec = _today_rec(g.me["id"])
    if not rec:
        raise ApiError(409, "not_checked_in", "Check in first before checking out.")
    if rec["check_out"]:
        raise ApiError(409, "already_checked_out", "You have already checked out today.")
    t = P.now().strftime("%H:%M:%S")
    P.db().execute("UPDATE attendance SET check_out=? WHERE id=?", (t, rec["id"]))
    P.db().commit()
    return ok(today=att_json(_today_rec(g.me["id"])), message="Checked out at %s. See you tomorrow." % P.t12(t))


@bp.get("/attendance/mine")
@api_auth()
def my_attendance():
    a = request.args
    start = valid_date(a.get("from") or P.now().strftime("%Y-%m-01"), "From date")
    end = valid_date(a.get("to") or P.now().strftime("%Y-%m-%d"), "To date")
    rows = P.db().execute("SELECT * FROM attendance WHERE user_id=? AND work_date BETWEEN ? AND ? ORDER BY work_date DESC",
                          (g.me["id"], start, end)).fetchall()
    return ok(items=[att_json(r) for r in rows])


# ------------------------------------------------------------------ admin: overview / attendance
@bp.get("/admin/overview")
@api_auth(admin=True)
def admin_overview():
    n = P.now()
    today = n.strftime("%Y-%m-%d")
    present = P.db().execute(
        "SELECT a.*, u.full_name, u.emp_id, u.department, u.photo, u.designation FROM attendance a "
        "JOIN users u ON u.id=a.user_id WHERE a.work_date=? ORDER BY a.check_in DESC", (today,)).fetchall()
    absent = P.db().execute(
        "SELECT * FROM users WHERE role='employee' AND active=1 AND id NOT IN "
        "(SELECT user_id FROM attendance WHERE work_date=?) ORDER BY full_name", (today,)).fetchall()
    late = sum(1 for r in present if r["status"] == "Late")
    need = len(absent) + sum(1 for r in present if r["status"] in ("Late", "Absent"))
    items = []
    for r in present:
        d = att_json(r, with_person=True)
        d.update(has_photo=bool(r["photo"]), photo_v=r["photo"] or "", designation=r["designation"] or "")
        items.append(d)
    week = []
    for i in range(6, -1, -1):  # 7-day attendance trend for the dashboard chart
        day = (n - timedelta(days=i)).strftime("%Y-%m-%d")
        row = P.db().execute("SELECT SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) p, "
                             "SUM(CASE WHEN status='Late' THEN 1 ELSE 0 END) l FROM attendance WHERE work_date=?", (day,)).fetchone()
        week.append({"date": day, "present": int(row["p"] or 0), "late": int(row["l"] or 0)})
    return ok(date=today, total=len(present) + len(absent), checked_in=len(present), late=late, not_checked_in=len(absent),
              present=items, absent=[user_json(u, full=False) for u in absent], week=week,
              reminders_open=P.reminder_open(n), reminders_needed=need, **clock_info())


@bp.get("/admin/attendance")
@api_auth(admin=True)
def admin_attendance():
    a = request.args
    if a.get("from"):
        valid_date(a["from"], "From date")
    if a.get("to"):
        valid_date(a["to"], "To date")
    rows, start, end = P.attendance_query(limit=1000)
    items = [att_json(r, with_person=True) for r in rows]
    summary = {k: sum(1 for i in items if i["status"] == k) for k in STATUSES}
    return ok(items=items, start=start, end=end, count=len(items), summary=summary)


@bp.post("/admin/attendance")
@api_auth(admin=True)
def admin_attendance_save():
    d = body()
    uid = d.get("user_id")
    day = valid_date(s(d.get("date"), 10) or P.now().strftime("%Y-%m-%d"))
    st = s(d.get("status"), 12) or "Present"

    def tm(v):
        v = s(v, 8)
        if not v:
            return None
        if not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", v):
            raise ApiError(400, "bad_time", "Time must look like 09:05 or 09:05:00.")
        return v if len(v) == 8 else v + ":00"
    ci, co = tm(d.get("check_in")), tm(d.get("check_out"))
    emp = P.db().execute("SELECT id FROM users WHERE id=? AND role='employee'", (uid,)).fetchone() if uid else None
    if not emp or st not in STATUSES:
        raise ApiError(400, "invalid", "Choose an employee and a valid status.")
    P.db().execute(
        "INSERT INTO attendance(user_id,work_date,check_in,check_out,status,note,ip) VALUES(?,?,?,?,?,?, 'admin') "
        "ON CONFLICT(user_id,work_date) DO UPDATE SET check_in=excluded.check_in, "
        "check_out=excluded.check_out, status=excluded.status, note=excluded.note",
        (uid, day, ci, co, st, s(d.get("note"), 200)))
    P.db().commit()
    return ok(message="Attendance saved.")


# ------------------------------------------------------------------ admin: employees
def _employee(uid):
    e = P.db().execute("SELECT * FROM users WHERE id=? AND role='employee'", (uid,)).fetchone()
    if not e:
        raise ApiError(404, "not_found", "Employee not found.")
    return e


def _emp_fields(d):
    return dict(emp_id=s(d.get("emp_id"), 20).upper(), full_name=s(d.get("full_name"), 80), designation=s(d.get("designation"), 80),
                department=s(d.get("department"), 80), joined_on=s(d.get("joined_on"), 10))


def _check_emp_fields(f):
    if not re.fullmatch(r"[A-Z0-9_-]{3,20}", f["emp_id"]):
        raise ApiError(400, "bad_id", "Employee ID must be 3 to 20 letters, numbers, dash or underscore.")
    if not f["full_name"]:
        raise ApiError(400, "no_name", "Full name is required.")
    if f["joined_on"]:
        valid_date(f["joined_on"], "Joining date")


@bp.get("/admin/employees")
@api_auth(admin=True)
def admin_employees():
    rows = P.db().execute("SELECT * FROM users WHERE role='employee' ORDER BY active DESC, full_name").fetchall()
    today = {r["user_id"]: r for r in P.db().execute("SELECT * FROM attendance WHERE work_date=?", (P.now().strftime("%Y-%m-%d"),)).fetchall()}
    items = []
    for u in rows:
        d = user_json(u)
        t = today.get(u["id"])
        d["today_status"] = t["status"] if t else "Not checked in"
        items.append(d)
    return ok(items=items)


@bp.get("/admin/employees/next-id")
@api_auth(admin=True)
def admin_next_id():
    suggest, vacant = P.id_plan()
    return ok(suggest=suggest, vacant=vacant)


@bp.post("/admin/employees")
@api_auth(admin=True)
def admin_employee_create():
    d = body()
    f = _emp_fields(d)
    pw = s(d.get("password"), 64) or P.gen_password()
    _check_emp_fields(f)
    if len(pw) < 8:
        raise ApiError(400, "weak_password", "Temporary password must be at least 8 characters.")
    if P.db().execute("SELECT 1 FROM users WHERE emp_id=?", (f["emp_id"],)).fetchone():
        raise ApiError(409, "id_taken", "That Employee ID is already taken.")
    P.db().execute("INSERT INTO users(emp_id,full_name,designation,department,joined_on,password_hash,must_change) VALUES(?,?,?,?,?,?,1)",
                   (f["emp_id"], f["full_name"], f["designation"], f["department"], f["joined_on"], generate_password_hash(pw)))
    P.db().commit()
    new = P.db().execute("SELECT * FROM users WHERE emp_id=?", (f["emp_id"],)).fetchone()
    return ok(employee=user_json(new), temp_password=pw,
              message="Employee created. Share the login details; they must change the password at first login.")


@bp.get("/admin/employees/<int:uid>")
@api_auth(admin=True)
def admin_employee_get(uid):
    e = _employee(uid)
    rows = P.db().execute("SELECT * FROM attendance WHERE user_id=? ORDER BY work_date DESC LIMIT 31", (uid,)).fetchall()
    return ok(employee=user_json(e), stats=_month_stats(uid, P.now().strftime("%Y-%m")), history=[att_json(r) for r in rows])


@bp.put("/admin/employees/<int:uid>")
@api_auth(admin=True)
def admin_employee_update(uid):
    _employee(uid)
    f = _emp_fields(body())
    _check_emp_fields(f)
    if P.db().execute("SELECT 1 FROM users WHERE emp_id=? AND id<>?", (f["emp_id"], uid)).fetchone():
        raise ApiError(409, "id_taken", "That Employee ID is already taken.")
    P.db().execute("UPDATE users SET emp_id=?,full_name=?,designation=?,department=?,joined_on=? WHERE id=?",
                   (f["emp_id"], f["full_name"], f["designation"], f["department"], f["joined_on"], uid))
    P.db().commit()
    return ok(employee=user_json(_employee(uid)), message="Employee updated.")


@bp.post("/admin/employees/<int:uid>/reset")
@api_auth(admin=True)
def admin_employee_reset(uid):
    e = _employee(uid)
    pw = P.gen_password()
    P.db().execute("UPDATE users SET password_hash=?, must_change=1 WHERE id=?", (generate_password_hash(pw), uid))
    P.db().commit()
    return ok(emp_id=e["emp_id"], full_name=e["full_name"], temp_password=pw, message="New temporary password created.")


@bp.post("/admin/employees/<int:uid>/toggle")
@api_auth(admin=True)
def admin_employee_toggle(uid):
    e = _employee(uid)
    P.db().execute("UPDATE users SET active=? WHERE id=?", (0 if e["active"] else 1, uid))
    P.db().commit()
    return ok(employee=user_json(_employee(uid)), message="%s %s." % (e["full_name"], "deactivated" if e["active"] else "re-activated"))


@bp.delete("/admin/employees/<int:uid>")
@api_auth(admin=True)
def admin_employee_delete(uid):
    e = _employee(uid)
    P.db().execute("DELETE FROM attendance WHERE user_id=?", (uid,))
    P.db().execute("DELETE FROM photos WHERE user_id=?", (uid,))
    P.db().execute("DELETE FROM users WHERE id=?", (uid,))
    P.db().commit()
    return ok(message="%s (%s) was deleted permanently." % (e["full_name"], e["emp_id"]))


# ------------------------------------------------------------------ admin: reminders + settings
@bp.get("/admin/reminders")
@api_auth(admin=True)
def admin_reminders():
    n = P.now()
    items = [{"user_id": r["u"]["id"], "emp_id": r["u"]["emp_id"], "full_name": r["u"]["full_name"], "kind": r["kind"],
              "phone": r["phone"] or "", "message": r["msg"], "link": r["link"]} for r in P.reminder_rows(n)]
    return ok(items=items, is_open=P.reminder_open(n), reminder_time=P.setting("reminder_time"), date=n.strftime("%Y-%m-%d"))


def _settings_json():
    return {k: P.setting(k) for k in P.DEFAULT_SETTINGS}


@bp.get("/admin/settings")
@api_auth(admin=True)
def admin_settings():
    return ok(settings=_settings_json())


@bp.put("/admin/settings")
@api_auth(admin=True)
def admin_settings_save():
    from zoneinfo import ZoneInfo
    f = body()
    try:
        datetime.strptime(f["office_start"], "%H:%M")
        int(f["grace_minutes"])
        ZoneInfo(f["timezone"])
        datetime.strptime(f.get("reminder_time", "10:15"), "%H:%M")
        if not re.fullmatch(r"\d{1,4}", str(f.get("country_code", "92")).strip()):
            raise ValueError("country code")
    except Exception:
        raise ApiError(400, "bad_settings", "Please enter a valid start time (HH:MM), grace minutes, timezone (e.g. Asia/Karachi), "
                                            "reminder time and country code (e.g. 92).")
    vals = {k: str(f[k]).strip() for k in ("office_start", "grace_minutes", "timezone")}
    vals["reminder_time"] = str(f.get("reminder_time", "10:15")).strip()
    vals["country_code"] = str(f.get("country_code", "92")).strip()
    for k in ("msg_absent", "msg_late"):
        vals[k] = (str(f.get(k, "")).strip() or P.DEFAULT_SETTINGS[k])[:500]
    for k, v in vals.items():
        P.db().execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    P.db().commit()
    P._SETCACHE["v"] = None
    return ok(settings=_settings_json(), message="Settings saved.")


# ------------------------------------------------------------------ admin: finance
def _money_in(v):
    try:
        amt = Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ApiError(400, "bad_amount", "Enter a valid amount.")
    if amt <= 0 or amt > Decimal("100000000000"):
        raise ApiError(400, "bad_amount", "Amount must be greater than zero.")
    return int((amt * 100).quantize(Decimal("1")))


def tx_json(r):
    return {"id": r["id"], "date": r["tx_date"], "kind": r["kind"], "category": r["category"], "title": r["title"],
            "description": r["description"] or "", "amount": int(r["amount_minor"]) / 100.0, "method": r["method"] or "Cash",
            "created_by": r["created_by"] or ""}


def _tx_fields(d):
    kind = s(d.get("kind"), 10).lower() or "expense"
    if kind not in ("expense", "income"):
        raise ApiError(400, "bad_kind", "Type must be expense or income.")
    cats = EXPENSE_CATEGORIES if kind == "expense" else INCOME_CATEGORIES
    cat = s(d.get("category"), 40) or "Other"
    if cat not in cats:
        cat = "Other"
    title = s(d.get("title"), 120)
    if not title:
        raise ApiError(400, "no_title", "Please enter a title.")
    method = s(d.get("method"), 20) or "Cash"
    if method not in PAY_METHODS:
        method = "Other"
    return dict(tx_date=valid_date(s(d.get("date"), 10) or P.now().strftime("%Y-%m-%d")), kind=kind, category=cat, title=title,
                description=s(d.get("description"), 500), amount_minor=_money_in(d.get("amount")), method=method)


@bp.get("/admin/finance/meta")
@api_auth(admin=True)
def fin_meta():
    return ok(expense_categories=EXPENSE_CATEGORIES, income_categories=INCOME_CATEGORIES, methods=PAY_METHODS, currency="PKR")


@bp.get("/admin/finance/transactions")
@api_auth(admin=True)
def fin_list():
    a, n = request.args, P.now()
    start = valid_date(a.get("from") or n.strftime("%Y-%m-01"), "From date")
    end = valid_date(a.get("to") or n.strftime("%Y-%m-%d"), "To date")
    q = "SELECT * FROM transactions WHERE tx_date BETWEEN ? AND ?"
    p = [start, end]
    if a.get("kind") in ("expense", "income"):
        q += " AND kind=?"
        p.append(a["kind"])
    if a.get("category"):
        q += " AND category=?"
        p.append(a["category"])
    if a.get("q"):
        q += " AND (LOWER(title) LIKE ? OR LOWER(description) LIKE ?)"
        p += ["%" + a["q"].lower() + "%"] * 2
    q += " ORDER BY tx_date DESC, id DESC LIMIT 1000"
    rows = P.db().execute(q, p).fetchall()
    items = [tx_json(r) for r in rows]
    inc = sum(i["amount"] for i in items if i["kind"] == "income")
    exp = sum(i["amount"] for i in items if i["kind"] == "expense")
    return ok(items=items, start=start, end=end, income=round(inc, 2), expense=round(exp, 2), net=round(inc - exp, 2), count=len(items))


@bp.post("/admin/finance/transactions")
@api_auth(admin=True)
def fin_create():
    f = _tx_fields(body())
    P.db().execute("INSERT INTO transactions(tx_date,kind,category,title,description,amount_minor,method,created_by) VALUES(?,?,?,?,?,?,?,?)",
                   (f["tx_date"], f["kind"], f["category"], f["title"], f["description"], f["amount_minor"], f["method"], g.me["emp_id"]))
    P.db().commit()
    return ok(message="%s saved." % f["kind"].capitalize())


@bp.put("/admin/finance/transactions/<int:tid>")
@api_auth(admin=True)
def fin_update(tid):
    if not P.db().execute("SELECT 1 FROM transactions WHERE id=?", (tid,)).fetchone():
        raise ApiError(404, "not_found", "Entry not found.")
    f = _tx_fields(body())
    P.db().execute("UPDATE transactions SET tx_date=?,kind=?,category=?,title=?,description=?,amount_minor=?,method=? WHERE id=?",
                   (f["tx_date"], f["kind"], f["category"], f["title"], f["description"], f["amount_minor"], f["method"], tid))
    P.db().commit()
    return ok(message="Entry updated.")


@bp.delete("/admin/finance/transactions/<int:tid>")
@api_auth(admin=True)
def fin_delete(tid):
    if not P.db().execute("SELECT 1 FROM transactions WHERE id=?", (tid,)).fetchone():
        raise ApiError(404, "not_found", "Entry not found.")
    P.db().execute("DELETE FROM transactions WHERE id=?", (tid,))
    P.db().commit()
    return ok(message="Entry deleted.")


def _month_shift(first, delta):
    y, m = first.year, first.month - 1 + delta
    return first.replace(year=y + m // 12, month=m % 12 + 1, day=1)


@bp.get("/admin/finance/summary")
@api_auth(admin=True)
def fin_summary():
    n = P.now()
    month = s(request.args.get("month"), 7) or n.strftime("%Y-%m")
    try:
        first = datetime.strptime(month + "-01", "%Y-%m-%d")
    except ValueError:
        raise ApiError(400, "bad_month", "Month must look like 2026-11.")
    nxt = _month_shift(first, 1)
    days = (nxt - first).days
    pref = month + "%"
    db = P.db()
    tot = db.execute("SELECT SUM(CASE WHEN kind='income' THEN amount_minor ELSE 0 END) i, "
                     "SUM(CASE WHEN kind='expense' THEN amount_minor ELSE 0 END) e, COUNT(*) c "
                     "FROM transactions WHERE tx_date LIKE ?", (pref,)).fetchone()
    income, expense = int(tot["i"] or 0) / 100.0, int(tot["e"] or 0) / 100.0
    cats = db.execute("SELECT category, SUM(amount_minor) t, COUNT(*) c FROM transactions WHERE tx_date LIKE ? AND kind='expense' "
                      "GROUP BY category ORDER BY t DESC", (pref,)).fetchall()
    by_cat = [{"category": r["category"], "total": int(r["t"]) / 100.0, "count": int(r["c"]),
               "percent": round(100.0 * int(r["t"]) / max(1, int(tot["e"] or 0)), 1)} for r in cats]
    dayrows = {r["tx_date"]: r for r in db.execute(
        "SELECT tx_date, SUM(CASE WHEN kind='income' THEN amount_minor ELSE 0 END) i, "
        "SUM(CASE WHEN kind='expense' THEN amount_minor ELSE 0 END) e FROM transactions WHERE tx_date LIKE ? GROUP BY tx_date",
        (pref,)).fetchall()}
    by_day = []
    for i in range(days):
        key = (first + timedelta(days=i)).strftime("%Y-%m-%d")
        r = dayrows.get(key)
        by_day.append({"date": key, "income": int(r["i"] or 0) / 100.0 if r else 0.0, "expense": int(r["e"] or 0) / 100.0 if r else 0.0})
    trend = []
    for back in range(5, -1, -1):
        m0 = _month_shift(first, -back)
        r = db.execute("SELECT SUM(CASE WHEN kind='income' THEN amount_minor ELSE 0 END) i, "
                       "SUM(CASE WHEN kind='expense' THEN amount_minor ELSE 0 END) e FROM transactions WHERE tx_date LIKE ?",
                       (m0.strftime("%Y-%m") + "%",)).fetchone()
        trend.append({"month": m0.strftime("%Y-%m"), "label": m0.strftime("%b"), "income": int(r["i"] or 0) / 100.0,
                      "expense": int(r["e"] or 0) / 100.0})
    elapsed = days if month != n.strftime("%Y-%m") else max(1, n.day)
    biggest = db.execute("SELECT * FROM transactions WHERE tx_date LIKE ? AND kind='expense' ORDER BY amount_minor DESC LIMIT 1", (pref,)).fetchone()
    today_exp = db.execute("SELECT SUM(amount_minor) e FROM transactions WHERE tx_date=? AND kind='expense'", (n.strftime("%Y-%m-%d"),)).fetchone()
    return ok(month=month, income=income, expense=expense, net=round(income - expense, 2), count=int(tot["c"] or 0),
              avg_daily_expense=round(expense / elapsed, 2), today_expense=int(today_exp["e"] or 0) / 100.0,
              top_category=by_cat[0]["category"] if by_cat else "", biggest_expense=tx_json(biggest) if biggest else None,
              by_category=by_cat, by_day=by_day, trend=trend)


@bp.route("/<path:p>", methods=["GET", "POST", "PUT", "DELETE"])
def _not_found(p):
    raise ApiError(404, "not_found", "Unknown API endpoint.")
