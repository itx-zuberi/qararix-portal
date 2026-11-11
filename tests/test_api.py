import os, sys, tempfile, io, json
tmp = tempfile.mkdtemp()
os.environ["QARARIX_DATA"] = tmp
os.environ["QARARIX_ADMIN_PASSWORD"] = "Qararix@123"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import portal
c = portal.app.test_client()
B = "/api/v1"
def call(m, path, tok=None, **kw):
    h = {"Authorization": "Bearer " + tok} if tok else {}
    r = getattr(c, m)(B + path, headers=h, **kw)
    try: j = r.get_json()
    except Exception: j = None
    return r.status_code, j, r
def chk(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: chk.fails += 1
chk.fails = 0

s, j, _ = call("get", "/config"); chk(s == 200 and j["ok"], "config " + str(j.get("timezone")))
s, j, _ = call("get", "/me"); chk(s == 401, "me without token 401")
s, j, _ = call("post", "/auth/login", json={"emp_id": "admin", "password": "bad"}); chk(s == 401, "bad login 401")
s, j, _ = call("post", "/auth/login", json={"emp_id": "admin", "password": "Qararix@123"})
chk(s == 200 and j["must_change"], "admin login must_change"); tok = j["token"]
s, j, _ = call("get", "/admin/overview", tok); chk(s == 403 and j["error"]["code"] == "must_change", "blocked until pw change")
s, j, _ = call("post", "/auth/change-password", tok, json={"current": "Qararix@123", "new": "Admin@12345", "confirm": "Admin@12345"})
chk(s == 200 and j["token"] != tok, "change password returns new token"); new = j["token"]
s, j, _ = call("get", "/me", tok); chk(s == 401, "old token revoked")
tok = new
s, j, _ = call("get", "/admin/overview", tok); chk(s == 200 and j["total"] == 0, "overview empty")
s, j, _ = call("get", "/admin/employees/next-id", tok); chk(j["suggest"] == "QRX-001", "next id " + j["suggest"])
s, j, _ = call("post", "/admin/employees", tok, json={"emp_id": "QRX-001", "full_name": "Ali Khan", "designation": "Dev", "department": "IT"})
chk(s == 200 and len(j["temp_password"]) >= 8, "create employee"); eid = j["employee"]["id"]; tmp_pw = j["temp_password"]
s, j, _ = call("post", "/admin/employees", tok, json={"emp_id": "QRX-001", "full_name": "Dup"}); chk(s == 409, "dup id 409")
s, j, _ = call("post", "/admin/employees", tok, json={"emp_id": "x", "full_name": "Dup"}); chk(s == 400, "bad id 400")
# employee flow
s, j, _ = call("post", "/auth/login", json={"emp_id": "qrx-001", "password": tmp_pw}); chk(s == 200 and j["must_change"], "emp login")
et = j["token"]
s, j, _ = call("post", "/auth/change-password", et, json={"current": tmp_pw, "new": "Ali@123456", "confirm": "Ali@123456"}); et = j["token"]
s, j, _ = call("get", "/admin/overview", et); chk(s == 403 and j["error"]["code"] == "forbidden", "employee blocked from admin")
s, j, _ = call("get", "/dashboard", et); chk(s == 200 and j["today"] is None, "emp dashboard")
s, j, _ = call("post", "/checkout", et); chk(s == 409, "checkout before checkin 409")
s, j, _ = call("post", "/checkin", et); chk(s == 200 and j["today"]["check_in"], "checkin " + j["message"])
s, j, _ = call("post", "/checkin", et); chk(s == 409, "double checkin 409")
s, j, _ = call("post", "/checkout", et); chk(s == 200 and j["today"]["check_out"], "checkout")
s, j, _ = call("get", "/dashboard", et); chk(j["today"]["status"] in ("Present", "Late") and len(j["history"]) == 1, "dashboard after")
# profile
s, j, _ = call("put", "/me", et, json={"phone": "0300-1234567", "email": "ali@x.com", "cnic": "1234512345671", "dob": "1999-01-31", "address": "Abbottabad", "emg_name": "Dad", "emg_phone": "0311-1234567"})
chk(s == 200 and j["user"]["cnic"] == "12345-1234567-1" and j["user"]["completion"]["score"] == 85, "profile saved score=%s" % j["user"]["completion"]["score"])
s, j, _ = call("put", "/me", et, json={"phone": "abc"}); chk(s == 400, "bad phone 400")
from PIL import Image
buf = io.BytesIO(); Image.new("RGB", (50, 50), "red").save(buf, "JPEG"); buf.seek(0)
s, j, _ = call("post", "/me/photo", et, data={"photo": (buf, "a.jpg")}, content_type="multipart/form-data")
chk(s == 200 and j["user"]["has_photo"], "photo upload"); 
s, j2, r = call("get", "/avatar/%d" % eid, et); chk(s == 200 and r.mimetype == "image/jpeg", "avatar fetch")
s, j2, r = call("get", "/avatar/1", et); chk(s == 403, "avatar of other user 403")
s, j, _ = call("post", "/me/photo", et, data={"photo": (io.BytesIO(b"notimage"), "a.jpg")}, content_type="multipart/form-data"); chk(s == 400, "bad image 400")
# admin attendance
s, j, _ = call("get", "/admin/overview", tok); chk(j["checked_in"] == 1 and len(j["week"]) == 7, "overview checked_in=1")
s, j, _ = call("get", "/admin/attendance", tok); chk(j["count"] == 1, "attendance list")
s, j, _ = call("post", "/admin/attendance", tok, json={"user_id": eid, "date": "2026-11-02", "check_in": "09:05", "check_out": "17:30", "status": "Present"}); chk(s == 200, "admin add attendance")
s, j, _ = call("get", "/admin/attendance?from=2026-11-01&to=2026-11-30&q=ali", tok); chk(j["count"] >= 1 and j["items"][0]["full_name"] == "Ali Khan", "filter works")
s, j, _ = call("post", "/admin/attendance", tok, json={"user_id": eid, "date": "bad"}); chk(s == 400, "bad date")
s, j, _ = call("get", "/admin/employees/%d" % eid, tok); chk(s == 200 and j["stats"]["present"] + j["stats"]["late"] >= 1, "employee detail")
s, j, _ = call("put", "/admin/employees/%d" % eid, tok, json={"emp_id": "QRX-001", "full_name": "Ali K", "designation": "Lead", "department": "IT"}); chk(s == 200 and j["employee"]["full_name"] == "Ali K", "update employee")
s, j, _ = call("get", "/admin/reminders", tok); chk(s == 200, "reminders")
s, j, _ = call("get", "/admin/settings", tok); chk(j["settings"]["timezone"] == "Asia/Karachi", "settings get")
st = j["settings"]; st["grace_minutes"] = "20"
s, j, _ = call("put", "/admin/settings", tok, json=st); chk(s == 200 and j["settings"]["grace_minutes"] == "20", "settings put")
# finance
s, j, _ = call("get", "/admin/finance/meta", et); chk(s == 403, "finance blocked for employee")
today = portal.datetime.now().strftime("%Y-%m-%d")
for d, k, cat, t, a in [(today, "expense", "Rent", "Office rent", "45000"), (today, "expense", "Food", "Team lunch", 3200.50),
                        (today, "income", "Client Payment", "Invoice 12", "150000"), ("2026-10-05", "expense", "Travel", "Fuel", "9000")]:
    s, j, _ = call("post", "/admin/finance/transactions", tok, json={"date": d, "kind": k, "category": cat, "title": t, "amount": a, "description": "desc", "method": "Bank"})
    chk(s == 200, "add tx " + t)
s, j, _ = call("post", "/admin/finance/transactions", tok, json={"title": "x", "amount": "-5"}); chk(s == 400, "negative amount 400")
s, j, _ = call("post", "/admin/finance/transactions", tok, json={"title": "", "amount": "5"}); chk(s == 400, "empty title 400")
s, j, _ = call("get", "/admin/finance/transactions?from=2026-10-01&to=2026-12-31", tok)
chk(j["count"] == 4 and j["expense"] == 57200.5 and j["income"] == 150000, "tx list totals exp=%s inc=%s" % (j["expense"], j["income"]))
tid = j["items"][0]["id"]
s, j, _ = call("put", "/admin/finance/transactions/%d" % tid, tok, json={"date": today, "kind": "expense", "category": "Food", "title": "Edited", "amount": "100"}); chk(s == 200, "edit tx")
s, j, _ = call("get", "/admin/finance/summary", tok)
chk(s == 200 and len(j["by_day"]) >= 28 and len(j["trend"]) == 6 and j["net"] == j["income"] - j["expense"], "summary exp=%s inc=%s cats=%d" % (j["expense"], j["income"], len(j["by_category"])))
print(json.dumps({k: j[k] for k in ("month","income","expense","net","avg_daily_expense","top_category")}))
s, j, _ = call("delete", "/admin/finance/transactions/%d" % tid, tok); chk(s == 200, "delete tx")
s, j, _ = call("get", "/admin/finance/summary?month=2026-13", tok); chk(s == 400, "bad month")
# toggle/reset/delete
s, j, _ = call("post", "/admin/employees/%d/reset" % eid, tok); chk(s == 200 and j["temp_password"], "reset pw")
s, j, _ = call("get", "/dashboard", et); chk(s == 401, "emp token revoked after reset")
s, j, _ = call("post", "/admin/employees/%d/toggle" % eid, tok); chk(s == 200 and not j["employee"]["active"], "deactivate")
s, j, _ = call("post", "/auth/login", json={"emp_id": "QRX-001", "password": "x"}); chk(s == 401, "inactive cannot login")
s, j, _ = call("delete", "/admin/employees/%d" % eid, tok); chk(s == 200, "delete employee")
s, j, _ = call("get", "/nonexistent", tok); chk(s == 404 and j["error"]["code"] == "not_found", "json 404")
# website unaffected (only checked when the portal templates are next to the code, i.e. inside your repo)
if os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(portal.__file__)), "templates")):
    r = c.get("/login"); chk(r.status_code == 200, "website /login still works")
    r = c.post("/login", data={"emp_id": "ADMIN", "password": "x"}); chk(r.status_code == 400, "website csrf still enforced")
print("FAILS:", chk.fails)
