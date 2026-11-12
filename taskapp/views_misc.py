import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from .auth import admin_required, login_required
from .db import get_db
from .services import SCOPE_LABELS, roles

bp = Blueprint("misc", __name__)


# ---------------------------------------------------------------- notifications
@bp.route("/notifications")
@login_required
def notifications():
    rows = get_db().execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 200",
        (g.user["id"],),
    ).fetchall()
    return render_template("notifications.html", notifications=rows)


@bp.route("/notifications/<int:notif_id>/open", methods=("POST",))
@login_required
def open_notification(notif_id):
    db = get_db()
    n = db.execute("SELECT * FROM notifications WHERE id = ? AND user_id = ?", (notif_id, g.user["id"])).fetchone()
    if n is None:
        abort(404)
    db.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notif_id,))
    db.commit()
    link = n["link"] or ""
    return redirect(link if link.startswith("/") and not link.startswith("//") else url_for("misc.notifications"))


@bp.route("/notifications/read-all", methods=("POST",))
@login_required
def read_all():
    get_db().execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (g.user["id"],))
    get_db().commit()
    flash("ทำเครื่องหมายว่าอ่านแล้วทั้งหมด", "success")
    return redirect(url_for("misc.notifications"))


# ---------------------------------------------------------------- users (admin only)
@bp.route("/admin/users")
@admin_required
def users():
    db = get_db()
    dept = request.args.get("department", "")
    where, args = "", []
    if dept == "none":
        where = "WHERE u.department_id IS NULL"
    elif dept.isdigit():
        where, args = "WHERE u.department_id = ?", [int(dept)]
    rows = db.execute(
        f"""SELECT u.*, d.name AS department_name, v.name AS division_name FROM users u
            LEFT JOIN departments d ON d.id = u.department_id
            LEFT JOIN divisions v ON v.id = u.division_id
            {where} ORDER BY u.role, d.name IS NULL, d.name, v.name, u.full_name""", args).fetchall()
    return render_template("users.html", users=rows, departments=_departments(), dept_filter=dept)


def _departments():
    return db_rows("SELECT * FROM departments ORDER BY name")


def _divisions():
    return db_rows("SELECT * FROM divisions ORDER BY name")


def db_rows(sql, args=()):
    return get_db().execute(sql, args).fetchall()


@bp.route("/admin/users/new", methods=("GET", "POST"))
@bp.route("/admin/users/<int:user_id>/edit", methods=("GET", "POST"))
@admin_required
def user_form(user_id=None):
    db = get_db()
    user = None
    if user_id is not None:
        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            abort(404)
    if request.method == "POST":
        f = request.form
        data = {
            "username": f.get("username", "").strip(),
            "full_name": f.get("full_name", "").strip(),
            "position": f.get("position", "").strip(),
            "role": f.get("role", "staff"),
            "is_active": 1 if f.get("is_active") else 0,
            "department_id": f.get("department_id", type=int),
            "division_id": f.get("division_id", type=int),
        }
        password = f.get("password", "")
        errors = []
        if not data["username"] or not data["full_name"]:
            errors.append("กรุณากรอกชื่อผู้ใช้และชื่อ-นามสกุล")
        if data["role"] not in roles():
            errors.append("บทบาทไม่ถูกต้อง")
        if data["department_id"] and not db.execute(
                "SELECT 1 FROM departments WHERE id = ?", (data["department_id"],)).fetchone():
            errors.append("หน่วยงานไม่ถูกต้อง")
        if data["division_id"]:
            div = db.execute("SELECT department_id FROM divisions WHERE id = ?", (data["division_id"],)).fetchone()
            if div is None or div["department_id"] != data["department_id"]:
                errors.append("แผนกที่เลือกไม่ได้อยู่ในหน่วยงานนี้")
        role = roles().get(data["role"])
        if role and role["scope"] == "division" and not data["division_id"]:
            errors.append(f"บทบาท “{role['name']}” มีขอบเขต{SCOPE_LABELS['division']} ต้องระบุหน่วยงานและแผนก")
        if user is None and len(password) < 6:
            errors.append("รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร")
        if user is not None and password and len(password) < 6:
            errors.append("รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร")
        losing_admin = (user is not None and user["role"] == "admin" and user["is_active"]
                        and (data["role"] != "admin" or not data["is_active"]))
        if losing_admin and user["id"] == g.user["id"]:
            errors.append("ไม่สามารถลดสิทธิ์หรือปิดการใช้งานบัญชีของตนเองได้")
        elif losing_admin and db.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1").fetchone()[0] <= 1:
            errors.append("ต้องมีผู้ดูแลระบบที่ใช้งานอยู่อย่างน้อย 1 คน")
        if not errors:
            try:
                if user is None:
                    db.execute(
                        """INSERT INTO users (username, password_hash, full_name, position, role, is_active,
                                              department_id, division_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (data["username"], generate_password_hash(password), data["full_name"],
                         data["position"], data["role"], data["is_active"], data["department_id"], data["division_id"]),
                    )
                else:
                    db.execute(
                        """UPDATE users SET username = ?, full_name = ?, position = ?, role = ?, is_active = ?,
                                  department_id = ?, division_id = ? WHERE id = ?""",
                        (data["username"], data["full_name"], data["position"], data["role"], data["is_active"],
                         data["department_id"], data["division_id"], user["id"]),
                    )
                    if password:
                        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                                   (generate_password_hash(password), user["id"]))
                db.commit()
                flash("บันทึกข้อมูลผู้ใช้แล้ว", "success")
                return redirect(url_for("misc.users"))
            except sqlite3.IntegrityError:
                errors.append("ชื่อผู้ใช้นี้มีอยู่แล้ว")
        for e in errors:
            flash(e, "danger")
        user = dict(user or {}, **data)
    return render_template("user_form.html", user=user, is_new=user_id is None,
                           departments=_departments(), divisions=_divisions())
