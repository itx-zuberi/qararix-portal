"""Entry point. Real application lives in portal.py.
If portal.py fails to import, this shows why (set QARARIX_DEBUG=1 to see the full error in the browser)."""
import os

try:
    from portal import app, main
except Exception:  # pragma: no cover
    import traceback
    from flask import Flask
    _tb = traceback.format_exc()
    print(_tb, flush=True)
    app = Flask(__name__)

    @app.route("/", defaults={"p": ""})
    @app.route("/<path:p>")
    def _startup_failed(p):
        body = _tb if os.environ.get("QARARIX_DEBUG") == "1" else "The portal failed to start. Set QARARIX_DEBUG=1 to see details."
        return "<pre style='white-space:pre-wrap;font:14px monospace;padding:20px'>%s</pre>" % body.replace("<", "&lt;"), 500

    def main():
        raise SystemExit(_tb)

if __name__ == "__main__":
    main()
