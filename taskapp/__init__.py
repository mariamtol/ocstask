import os
import secrets
import time
from datetime import date, datetime
from pathlib import Path

from flask import Flask, abort, g, request, session, url_for

from . import db as dbmod
from .services import (PRIORITY_LABELS, STATUS_LABELS, academic_years, can_delegate_task, current_academic_year,
                       due_soon_days, get_setting, has_perm, is_admin, role_labels, sweep_due_notifications,
                       unread_count, user_scope)

THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
SWEEP_INTERVAL_SECONDS = 300


def create_app(test_config=None):
    base = Path(__file__).resolve().parent.parent
    app = Flask(__name__, instance_path=str(base / "instance"))
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or _load_secret(base / "instance"),
        DATABASE=str(base / "instance" / "taskapp.db"),
        UPLOAD_FOLDER=str(base / "uploads"),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,  # 20 MB per request
        ALLOWED_EXTENSIONS={"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv",
                            "jpg", "jpeg", "png", "gif", "zip", "rar"},
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    app.teardown_appcontext(dbmod.close_db)

    from .auth import bp as auth_bp, load_user
    from .views_dashboard import bp as dash_bp
    from .views_tasks import bp as tasks_bp
    from .views_reports import bp as reports_bp
    from .views_misc import bp as misc_bp
    from .views_admin import bp as admin_bp

    for bp in (auth_bp, dash_bp, tasks_bp, reports_bp, misc_bp, admin_bp):
        app.register_blueprint(bp)

    # Bring databases created by older versions up to date
    if os.path.exists(app.config["DATABASE"]):
        with app.app_context():
            dbmod.migrate_db()

    @app.cli.command("init-db")
    def init_db_command():
        """Create empty tables (drops existing data)."""
        dbmod.init_db()
        print("Initialized the database.")

    last_sweep = {"at": 0.0}

    @app.before_request
    def before_request():
        load_user()
        if request.method == "POST":
            token = request.form.get("csrf_token")
            if not token or token != session.get("csrf_token"):
                abort(400, "CSRF token ไม่ถูกต้อง กรุณารีเฟรชหน้าแล้วลองใหม่")
        # Due-date notifications are generated lazily, at most every few minutes
        if g.user is not None and time.time() - last_sweep["at"] > SWEEP_INTERVAL_SECONDS:
            last_sweep["at"] = time.time()
            sweep_due_notifications()

    @app.context_processor
    def inject_globals():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        user = g.get("user")
        return {
            # Permission flags for templates (from the user's role, editable in "จัดการบทบาท"); data is scoped server-side
            "is_mgr": has_perm(user, "dashboard.overview"),
            "can_assign": user is not None and can_delegate_task(user),
            "can_review": has_perm(user, "report.review"),
            "can_create": has_perm(user, "task.create"),
            "scope": user_scope(user) if user else "own",
            "is_div_head": user is not None and user_scope(user) == "division",
            "tasks_label": ({"all": "งานทั้งหมด", "division": "งานในแผนก"}.get(user_scope(user), "งานของฉัน")
                            if user else "งานของฉัน"),
            "is_admin": is_admin(user),
            "academic_years": academic_years() if user else [],
            "current_year": current_academic_year() if user else None,
            "due_soon_days": due_soon_days(),
            "org_name": get_setting("org_name"),
            "csrf_token": session["csrf_token"],
            "STATUS_LABELS": STATUS_LABELS,
            "PRIORITY_LABELS": PRIORITY_LABELS,
            "ROLE_LABELS": role_labels() if user else {},
            "unread_notifications": unread_count(g.user["id"]) if g.get("user") else 0,
            "today": date.today().isoformat(),
        }

    @app.template_global()
    def static_url(filename):
        """Static file URL with the file's modification time, so browsers fetch updated CSS/JS right away."""
        try:
            version = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            version = 0
        return url_for("static", filename=filename, v=version)

    @app.template_filter("thaidate")
    def thaidate(value, with_time=False):
        if not value:
            return "-"
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return value
        text = f"{dt.day} {THAI_MONTHS[dt.month - 1]} {dt.year + 543}"
        if with_time:
            text += f" {dt:%H:%M} น."
        return text

    @app.template_filter("filesize")
    def filesize(n):
        for unit in ("B", "KB", "MB"):
            if n < 1024:
                return f"{n:.0f} {unit}"
            n /= 1024
        return f"{n:.1f} GB"

    @app.template_filter("pct")
    def pct(value):
        value = float(value or 0)
        return f"{value:.0f}" if value == int(value) else f"{value:.1f}"

    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(400)
    @app.errorhandler(413)
    def handle_error(err):
        from flask import render_template
        return render_template("error.html", error=err), err.code

    return app


def _load_secret(instance_dir):
    instance_dir.mkdir(parents=True, exist_ok=True)
    key_file = instance_dir / "secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))
    return key_file.read_text().strip()
