# Qararix Attendance Portal

A secure, web-based attendance system for Qararix. Employees sign in with an ID and password that **you** assign, check in and out daily, and maintain a profile. You (the admin) see every record in a table, manage employees, and export to CSV.

**Stack:** Python 3.10+ · Flask · SQLite (local) or PostgreSQL (hosted) · Gunicorn/Waitress

---

## 1. Quick start (Windows)

1. Install **Python 3.10 or newer** from python.org (tick *"Add Python to PATH"* during install).
2. Unzip this folder anywhere, e.g. `C:\qararix-portal`.
3. Double-click **`run_windows.bat`**. The first run creates a virtual environment and installs the requirements (needs internet once).
4. Open **http://localhost:5000** in your browser.

**Mac / Linux:** `chmod +x run.sh && ./run.sh`

### First login (admin)
| Employee ID | Password |
|---|---|
| `ADMIN` | `Qararix@123` |

You will be forced to change this password immediately. To choose your own starting password, set `QARARIX_ADMIN_PASSWORD` before the very first run.

---

## 2. How it works

### Admin
- **Overview** – today's totals: active employees, checked in, late, not checked in (with a "Mark" shortcut).
- **Attendance** – full table: employee, ID, date, check-in, check-out, hours, status. Filter by date range, employee, status or name/ID. **Download CSV** exports exactly what the filters show (opens in Excel).
- **Add / correct** – create or fix a record (forgot to check in, leave, absent).
- **Employees** – add employees (ID auto-suggested as `QRX-001`, `QRX-002`…, editable), see each person's profile and completion %, **Reset password** (generates a temporary one), **Deactivate** (blocks login, keeps history).
- **Settings** – office start time, grace minutes (after which check-in is marked **Late**), timezone (default `Asia/Karachi`).

When you create an employee or reset a password, the temporary login is shown **once** in a dark banner. Send it to the employee; they must change it at first login.

### Employee
- **My attendance** – live clock, one-tap **Check in** / **Check out**, monthly on-time and late counts, 31-day history.
- **My profile** – photo, phone, email, CNIC, date of birth, address, emergency contact. Name/ID/designation/department are admin-managed.
- **Password** – change anytime.

### Profile completion score
| Item | Weight |
|---|---|
| Profile photo | 15% |
| Phone number | 15% |
| Email address | 15% |
| CNIC | 15% |
| Home address | 15% |
| Emergency contact (name + phone) | 15% |
| Date of birth | 10% |

Edit the weights in `PROFILE_FIELDS` in `app.py`.

### Rules built in
- One record per employee per day. Check-in and check-out use the **server's clock**, so employees cannot fake times from their device.
- Only one check-in per day; check-out only after check-in, once.
- Photos are stored in the database, shown only to the owner and the admin.
- Passwords are hashed (never stored in plain text). Minimum 8 chars with a letter and a number.
- Login lockout: 5 wrong attempts per ID + IP → 10-minute wait.
- CSRF protection on every form, secure session cookies, uploaded photos verified by file signature (JPG/PNG/WEBP, max 3 MB) and 

---

## 3. Deploying online (so employees can use a link remotely)

### How the database works
The app supports **two databases with the same code**; it picks one automatically:

| You set | Database used | Best for |
|---|---|---|
| nothing | **SQLite** file `data/qararix.db` | Your laptop, or one VPS you control |
| `DATABASE_URL=postgresql://...` | **PostgreSQL** (hosted) | Cloud hosting where the app's disk is temporary |

Profile photos are stored **inside the database** (not as files), so nothing important lives on the web server's disk. That makes it safe on any host: redeploys and restarts never lose data. Everyone who opens your link talks to the same running app and the same database, so there is nothing to synchronise.

> Cloud hosts (Render, Railway, Heroku, Koyeb, Fly...) wipe local files on every redeploy. **Do not use SQLite there.** Use PostgreSQL via `DATABASE_URL`.

### Recommended: hosted web app + free PostgreSQL (about 15 minutes)
1. **Create a PostgreSQL database** at a provider with a free tier, e.g. **Neon** or **Supabase**. Copy the connection string (it looks like `postgresql://user:pass@host/db?sslmode=require`). Check each provider's current free limits.
2. **Put this folder on GitHub** (a private repo is fine; `.gitignore` already excludes local data).
3. **Create a Web Service** on a host such as **Render** or **Railway**, connected to that repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --preload --workers 2 --threads 4 --bind 0.0.0.0:$PORT` (the included `Procfile` does this)
   - Or choose the Docker option: the included `Dockerfile` works as-is.
4. **Set environment variables** on the host (see `.env.example`):
   - `DATABASE_URL`: the connection string from step 1
   - `SECRET_KEY`: any long random string (keeps logins valid across restarts)
   - `QARARIX_ADMIN_PASSWORD`: your first admin password (if you skip it, a random one is printed once in the host's logs)
   - `QARARIX_HTTPS=1`
5. Deploy. The tables and the `ADMIN` account are created automatically on first start. Open the host's URL, log in as `ADMIN`, change the password, then add employees.
6. Optional: attach your own domain such as `attendance.qararix.com` (the host's "Custom domain" setting), which gives a permanent professional link.

**Free-tier caveat:** many free web services go to sleep after inactivity, so the first page load after a quiet period can take 30 to 60 seconds. For reliable daily check-ins, use a low-cost paid instance (a few dollars a month) or the VPS option below.

### Alternative: Vercel (serverless) + Supabase
Works with the same `DATABASE_URL`. Import the GitHub repo in Vercel (New Project), leave build settings empty (Flask is auto-detected from `app.py`), and add the same environment variables. Static files are served from `public/static`. Notes: use the Supabase **Session pooler** string (switch to the Transaction pooler, port 6543, if you see "max clients reached"); the login lockout is per server instance, so use strong passwords; Vercel's free Hobby plan is for non-commercial use, so check their terms for a company.

### Alternative: your own VPS (about $4-6/month)
Run the app on a small Linux server with `./run.sh` (or as a `systemd` service) behind **Caddy** or **Nginx** for free HTTPS. You can keep the default SQLite file (back up `data/`) or point `DATABASE_URL` at PostgreSQL on the same server.

### Alternative: your laptop + tunnel (testing only)
Run it locally, then share it with `cloudflared tunnel --url http://localhost:5000` or `ngrok http 5000`. The laptop must stay on and online, so this is not recommended for daily use.

### Test locally before deploying
`python app.py` (SQLite), or to test PostgreSQL: set `DATABASE_URL` first, then `python app.py`.

## 4. Configuration (environment variables)
| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | empty (SQLite) | PostgreSQL connection string |
| `SECRET_KEY` | auto file in `data/` | Signs login sessions; set it on cloud hosts |
| `QARARIX_ADMIN_PASSWORD` | `Qararix@123` locally, random on PostgreSQL | First admin password (first start only) |
| `QARARIX_HTTPS` | off | Set to `1` when served over https (secure cookies) |
| `PORT` / `QARARIX_PORT` | `5000` | Port (hosts set `PORT` for you) |
| `QARARIX_HOST` | `0.0.0.0` | `127.0.0.1` = only this computer |
| `QARARIX_DATA` | `./data` | Folder for the SQLite file and key (local mode) |
| `TRUST_PROXY` | auto with PostgreSQL | Set `1` if behind a proxy while using SQLite |

## 5. Backup & restore
- **PostgreSQL:** use your provider's backups, or run `pg_dump "$DATABASE_URL" > backup.sql` regularly. Restore with `psql "$DATABASE_URL" < backup.sql`.
- **SQLite (local/VPS):** copy the `data/` folder (contains `qararix.db` and `secret.key`) somewhere safe, ideally daily. Never share it publicly.
- To move from SQLite to PostgreSQL later, export attendance to CSV from the admin page and re-create employees, or ask for a migration script.

## 6. Project layout
```
app.py              entry point (shows startup errors when QARARIX_DEBUG=1)
portal.py           all routes, DB logic, validation
templates/          HTML pages (Jinja)
public/static       branding, styles, logo (served by the CDN on Vercel)
data/               created on first run when using SQLite (database, key)
Procfile, Dockerfile, .env.example   cloud deployment
requirements.txt    Flask, Waitress, Gunicorn, psycopg2, tzdata
run_windows.bat / run.sh
```

## 7. Troubleshooting
- **"Python not found"** – reinstall Python and tick *Add to PATH*.
- **Others can't open the link** – check the app is running, the firewall allows it, and you're using the laptop's IP (not `localhost`).
- **Forgot admin password** – stop the app, delete `data/qararix.db` **only if you accept losing all data**, or run:
  `python -c "import sqlite3;from werkzeug.security import generate_password_hash as g;c=sqlite3.connect('data/qararix.db');c.execute(\"UPDATE users SET password_hash=?,must_change=1 WHERE emp_id='ADMIN'\",(g('NewPass@123'),));c.commit()"`
- **Wrong late/on-time** – adjust Settings → timezone / start time.
- Fonts load from Google Fonts; offline they fall back to system fonts automatically.

© Qararix – Intelligence That Decides.
