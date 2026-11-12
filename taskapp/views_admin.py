"""System administration: overview, academic years, settings and maintenance (admin only)."""
import os
import sqlite3
import uuid
from datetime import date

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .auth import admin_required
from .db import academic_year_bounds, get_db
from .permissions import ADMIN_ROLE, PERMISSIONS, SCOPE_HELP, SCOPE_LABELS
from .services import STATUS_LABELS, roles, set_setting

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@admin_required
def index():
    db = get_db()
    role_counts = {r["role"]: (r["total"], r["active"]) for r in db.execute(
        "SELECT role, COUNT(*) AS total, SUM(is_active) AS active FROM users GROUP BY role")}
    year_rows = db.execute(
        """SELECT ay.*, COUNT(t.id) AS task_count,
                  SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) AS done_count
           FROM academic_years ay LEFT JOIN tasks t ON t.academic_year_id = ay.id
           GROUP BY ay.id ORDER BY ay.year_be DESC""").fetchall()
    folder = current_app.config["UPLOAD_FOLDER"]
    files = [os.path.join(folder, f) for f in os.listdir(folder)] if os.path.isdir(folder) else []
    stats = {
        "tasks": db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
        "unassigned_year": db.execute("SELECT COUNT(*) FROM tasks WHERE academic_year_id IS NULL").fetchone()[0],
        "reports": db.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
        "attachments": len(files),
        "storage": sum(os.path.getsize(f) for f in files),
        "notifications": db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0],
        "old_read_notifications": db.execute(
            "SELECT COUNT(*) FROM notifications WHERE is_read = 1 AND created_at < datetime('now', 'localtime', '-90 days')"
        ).fetchone()[0],
    }
    return render_template("admin/index.html", role_counts=role_counts, years=year_rows, stats=stats,
                           roles_list=list(roles().values()), STATUS_LABELS=STATUS_LABELS)


@bp.route("/settings", methods=("POST",))
@admin_required
def settings():
    org_name = request.form.get("org_name", "").strip()
    days = request.form.get("due_soon_days", type=int)
    if not org_name or days is None or not 0 <= days <= 60:
        flash("กรุณากรอกชื่อหน่วยงาน และจำนวนวันแจ้งเตือนระหว่าง 0–60 วัน", "danger")
    else:
        set_setting("org_name", org_name)
        set_setting("due_soon_days", days)
        get_db().commit()
        flash("บันทึกการตั้งค่าระบบแล้ว", "success")
    return redirect(url_for("admin.index"))


@bp.route("/maintenance/notifications", methods=("POST",))
@admin_required
def cleanup_notifications():
    cur = get_db().execute(
        "DELETE FROM notifications WHERE is_read = 1 AND created_at < datetime('now', 'localtime', '-90 days')")
    get_db().commit()
    flash(f"ลบการแจ้งเตือนที่อ่านแล้วและเก่ากว่า 90 วัน จำนวน {cur.rowcount} รายการ", "success")
    return redirect(url_for("admin.index"))


# ---------------------------------------------------------------- academic years
def _year_or_404(year_id):
    year = get_db().execute("SELECT * FROM academic_years WHERE id = ?", (year_id,)).fetchone()
    if year is None:
        abort(404)
    return year


@bp.route("/academic-years")
@admin_required
def years():
    rows = get_db().execute(
        """SELECT ay.*, COUNT(t.id) AS task_count FROM academic_years ay
           LEFT JOIN tasks t ON t.academic_year_id = ay.id GROUP BY ay.id ORDER BY ay.year_be DESC""").fetchall()
    today = date.today()
    next_be = max([r["year_be"] for r in rows] + [(today.year if today.month >= 8 else today.year - 1) + 542]) + 1
    start, end = academic_year_bounds(next_be)
    return render_template("admin/years.html", years=rows,
                           new_year={"year_be": next_be, "start_date": start, "end_date": end},
                           today=today.isoformat())


@bp.route("/academic-years/new", methods=("POST",))
@bp.route("/academic-years/<int:year_id>/edit", methods=("GET", "POST"))
@admin_required
def year_form(year_id=None):
    year = _year_or_404(year_id) if year_id else None
    if request.method == "GET":
        return render_template("admin/year_form.html", year=year)

    year_be = request.form.get("year_be", type=int)
    errors = []
    if not year_be or not 2400 <= year_be <= 2800:
        errors.append("กรุณากรอกปีการศึกษาเป็นปี พ.ศ. (เช่น 2569)")
    else:
        default_start, default_end = academic_year_bounds(year_be)
        start = request.form.get("start_date") or default_start
        end = request.form.get("end_date") or default_end
        try:
            start, end = date.fromisoformat(start).isoformat(), date.fromisoformat(end).isoformat()
        except ValueError:
            errors.append("รูปแบบวันที่ไม่ถูกต้อง")
        else:
            if start >= end:
                errors.append("วันเริ่มต้นต้องมาก่อนวันสิ้นสุด")
            overlap = get_db().execute(
                "SELECT year_be FROM academic_years WHERE id != ? AND start_date <= ? AND end_date >= ?",
                (year_id or 0, end, start)).fetchone()
            if overlap:
                errors.append(f"ช่วงวันที่ซ้อนทับกับปีการศึกษา {overlap['year_be']}")
    if not errors:
        db = get_db()
        try:
            if year is None:
                db.execute("INSERT INTO academic_years (year_be, start_date, end_date) VALUES (?, ?, ?)",
                           (year_be, start, end))
            else:
                db.execute("UPDATE academic_years SET year_be = ?, start_date = ?, end_date = ? WHERE id = ?",
                           (year_be, start, end, year_id))
            db.commit()
            flash(f"บันทึกปีการศึกษา {year_be} แล้ว", "success")
            return redirect(url_for("admin.years"))
        except sqlite3.IntegrityError:
            errors.append(f"มีปีการศึกษา {year_be} อยู่แล้ว")
    for e in errors:
        flash(e, "danger")
    return redirect(url_for("admin.year_form", year_id=year_id) if year_id else url_for("admin.years"))


@bp.route("/academic-years/<int:year_id>/current", methods=("POST",))
@admin_required
def set_current_year(year_id):
    year = _year_or_404(year_id)
    db = get_db()
    db.execute("UPDATE academic_years SET is_current = (id = ?)", (year_id,))
    db.commit()
    flash(f"ตั้งปีการศึกษา {year['year_be']} เป็นปีปัจจุบันแล้ว", "success")
    return redirect(url_for("admin.years"))


@bp.route("/academic-years/<int:year_id>/delete", methods=("POST",))
@admin_required
def delete_year(year_id):
    year = _year_or_404(year_id)
    db = get_db()
    used = db.execute("SELECT COUNT(*) FROM tasks WHERE academic_year_id = ?", (year_id,)).fetchone()[0]
    if used:
        flash(f"ไม่สามารถลบปีการศึกษา {year['year_be']} ได้ เนื่องจากมีงาน {used} รายการอยู่ในปีนี้", "danger")
    elif year["is_current"]:
        flash("ไม่สามารถลบปีการศึกษาปัจจุบันได้ กรุณาตั้งปีอื่นเป็นปีปัจจุบันก่อน", "danger")
    else:
        db.execute("DELETE FROM academic_years WHERE id = ?", (year_id,))
        db.commit()
        flash(f"ลบปีการศึกษา {year['year_be']} แล้ว", "success")
    return redirect(url_for("admin.years"))


# ---------------------------------------------------------------- departments (หน่วยงาน) & divisions (แผนก)
def _row_or_404(table, row_id):
    row = get_db().execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _clean_name(field="name"):
    name = " ".join(request.form.get(field, "").split())
    if not name or len(name) > 120:
        flash("กรุณากรอกชื่อ (ไม่เกิน 120 ตัวอักษร)", "danger")
        return None
    return name


def _back_to_departments(anchor=None):
    return redirect(url_for("admin.departments") + (f"#dept-{anchor}" if anchor else ""))


@bp.route("/departments")
@admin_required
def departments():
    db = get_db()
    depts = [dict(r) for r in db.execute(
        """SELECT d.*, (SELECT COUNT(*) FROM users u WHERE u.department_id = d.id) AS user_count
           FROM departments d ORDER BY d.name""")]
    divs = db.execute(
        """SELECT v.*, (SELECT COUNT(*) FROM users u WHERE u.division_id = v.id) AS user_count
           FROM divisions v ORDER BY v.name""").fetchall()
    for d in depts:
        d["divisions"] = [v for v in divs if v["department_id"] == d["id"]]
    unassigned = db.execute("SELECT COUNT(*) FROM users WHERE department_id IS NULL").fetchone()[0]
    return render_template("admin/departments.html", departments=depts, unassigned=unassigned)


@bp.route("/departments/new", methods=("POST",))
@admin_required
def department_new():
    name = _clean_name()
    if name:
        try:
            cur = get_db().execute("INSERT INTO departments (name) VALUES (?)", (name,))
            get_db().commit()
            flash(f"เพิ่มหน่วยงาน “{name}” แล้ว", "success")
            return _back_to_departments(cur.lastrowid)
        except sqlite3.IntegrityError:
            flash(f"มีหน่วยงาน “{name}” อยู่แล้ว", "danger")
    return _back_to_departments()


@bp.route("/departments/<int:dept_id>/edit", methods=("POST",))
@admin_required
def department_edit(dept_id):
    _row_or_404("departments", dept_id)
    name = _clean_name()
    if name:
        try:
            get_db().execute("UPDATE departments SET name = ? WHERE id = ?", (name, dept_id))
            get_db().commit()
            flash(f"เปลี่ยนชื่อหน่วยงานเป็น “{name}” แล้ว", "success")
        except sqlite3.IntegrityError:
            flash(f"มีหน่วยงาน “{name}” อยู่แล้ว", "danger")
    return _back_to_departments(dept_id)


@bp.route("/departments/<int:dept_id>/delete", methods=("POST",))
@admin_required
def department_delete(dept_id):
    dept = _row_or_404("departments", dept_id)
    db = get_db()
    used = db.execute("SELECT COUNT(*) FROM users WHERE department_id = ?", (dept_id,)).fetchone()[0]
    if used:
        flash(f"ไม่สามารถลบหน่วยงาน “{dept['name']}” ได้ เนื่องจากมีผู้ใช้ {used} คนสังกัดอยู่", "danger")
        return _back_to_departments(dept_id)
    db.execute("DELETE FROM departments WHERE id = ?", (dept_id,))  # divisions cascade
    db.commit()
    flash(f"ลบหน่วยงาน “{dept['name']}” แล้ว", "success")
    return _back_to_departments()


@bp.route("/departments/<int:dept_id>/divisions/new", methods=("POST",))
@admin_required
def division_new(dept_id):
    dept = _row_or_404("departments", dept_id)
    name = _clean_name()
    if name:
        try:
            get_db().execute("INSERT INTO divisions (department_id, name) VALUES (?, ?)", (dept_id, name))
            get_db().commit()
            flash(f"เพิ่มแผนก “{name}” ใน{dept['name']}แล้ว", "success")
        except sqlite3.IntegrityError:
            flash(f"มีแผนก “{name}” ในหน่วยงานนี้อยู่แล้ว", "danger")
    return _back_to_departments(dept_id)


@bp.route("/divisions/<int:div_id>/edit", methods=("POST",))
@admin_required
def division_edit(div_id):
    div = _row_or_404("divisions", div_id)
    name = _clean_name()
    if name:
        try:
            get_db().execute("UPDATE divisions SET name = ? WHERE id = ?", (name, div_id))
            get_db().commit()
            flash(f"เปลี่ยนชื่อแผนกเป็น “{name}” แล้ว", "success")
        except sqlite3.IntegrityError:
            flash(f"มีแผนก “{name}” ในหน่วยงานนี้อยู่แล้ว", "danger")
    return _back_to_departments(div["department_id"])


@bp.route("/divisions/<int:div_id>/delete", methods=("POST",))
@admin_required
def division_delete(div_id):
    div = _row_or_404("divisions", div_id)
    db = get_db()
    used = db.execute("SELECT COUNT(*) FROM users WHERE division_id = ?", (div_id,)).fetchone()[0]
    if used:
        flash(f"ไม่สามารถลบแผนก “{div['name']}” ได้ เนื่องจากมีผู้ใช้ {used} คนสังกัดอยู่", "danger")
    else:
        db.execute("DELETE FROM divisions WHERE id = ?", (div_id,))
        db.commit()
        flash(f"ลบแผนก “{div['name']}” แล้ว", "success")
    return _back_to_departments(div["department_id"])


# ---------------------------------------------------------------- roles & permissions (บทบาทและสิทธิ์)
def _roles_with_counts():
    rows = get_db().execute(
        """SELECT r.*, (SELECT COUNT(*) FROM users u WHERE u.role = r.code) AS user_count,
                  (SELECT COUNT(*) FROM users u WHERE u.role = r.code AND u.is_active = 1
                     AND u.division_id IS NULL) AS no_division_count
           FROM roles r ORDER BY r.sort_order, r.id""").fetchall()
    info = roles()
    return [dict(r, perms=info[r["code"]]["perms"]) for r in rows]


@bp.route("/roles", endpoint="roles")
@admin_required
def roles_page():
    return render_template("admin/roles.html", roles=_roles_with_counts(), PERMISSIONS=PERMISSIONS,
                           SCOPE_LABELS=SCOPE_LABELS, SCOPE_HELP=SCOPE_HELP, ADMIN_ROLE=ADMIN_ROLE)


@bp.route("/roles/permissions", methods=("POST",))
@admin_required
def save_permissions():
    """Save the whole matrix: scope + permission checkboxes for every role except the locked admin role."""
    db = get_db()
    changed = 0
    warnings = []
    for r in db.execute("SELECT * FROM roles WHERE code != ?", (ADMIN_ROLE,)).fetchall():
        scope = request.form.get(f"scope:{r['id']}", r["scope"])
        if scope not in SCOPE_LABELS:
            abort(400)
        perms = {p for p in PERMISSIONS if request.form.get(f"perm:{r['id']}:{p}")}
        current = {x["permission"] for x in db.execute(
            "SELECT permission FROM role_permissions WHERE role_id = ?", (r["id"],))}
        if scope != r["scope"] or perms != current:
            changed += 1
            db.execute("UPDATE roles SET scope = ? WHERE id = ?", (scope, r["id"]))
            db.execute("DELETE FROM role_permissions WHERE role_id = ?", (r["id"],))
            db.executemany("INSERT INTO role_permissions (role_id, permission) VALUES (?, ?)",
                           [(r["id"], p) for p in sorted(perms)])
            if scope == "division":
                missing = db.execute("SELECT COUNT(*) FROM users WHERE role = ? AND is_active = 1 AND division_id IS NULL",
                                     (r["code"],)).fetchone()[0]
                if missing:
                    warnings.append(f"ผู้ใช้บทบาท “{r['name']}” {missing} คนยังไม่ระบุแผนก "
                                    "จึงจะเห็นเฉพาะงานของตนจนกว่าจะกำหนดแผนก")
    db.commit()
    g.pop("roles", None)
    flash(f"บันทึกสิทธิ์แล้ว ({changed} บทบาทมีการเปลี่ยนแปลง)" if changed else "ไม่มีการเปลี่ยนแปลงสิทธิ์",
          "success" if changed else "info")
    for w in warnings:
        flash(w, "warning")
    return redirect(url_for("admin.roles"))


def _role_form():
    name = " ".join(request.form.get("name", "").split())
    desc = request.form.get("description", "").strip()[:300]
    if not name or len(name) > 60:
        flash("กรุณากรอกชื่อบทบาท (ไม่เกิน 60 ตัวอักษร)", "danger")
        return None, None
    return name, desc


@bp.route("/roles/new", methods=("POST",))
@admin_required
def role_new():
    name, desc = _role_form()
    if name is None:
        return redirect(url_for("admin.roles"))
    scope = request.form.get("scope", "own")
    if scope not in SCOPE_LABELS:
        abort(400)
    db = get_db()
    base = db.execute("SELECT * FROM roles WHERE code = ? AND code != ?",
                      (request.form.get("copy_from", ""), ADMIN_ROLE)).fetchone()
    try:
        cur = db.execute("INSERT INTO roles (code, name, description, scope, is_system, sort_order) "
                         "VALUES (?, ?, ?, ?, 0, 50)", (f"tmp-{uuid.uuid4().hex}", name, desc, scope))
    except sqlite3.IntegrityError:
        flash(f"มีบทบาทชื่อ “{name}” อยู่แล้ว", "danger")
        return redirect(url_for("admin.roles"))
    role_id = cur.lastrowid
    db.execute("UPDATE roles SET code = ? WHERE id = ?", (f"custom_{role_id}", role_id))
    if base is not None:
        db.execute("INSERT INTO role_permissions (role_id, permission) "
                   "SELECT ?, permission FROM role_permissions WHERE role_id = ?", (role_id, base["id"]))
    db.commit()
    flash(f"เพิ่มบทบาท “{name}” แล้ว" + (f" (คัดลอกสิทธิ์จาก “{base['name']}”)" if base else "")
          + " — ปรับสิทธิ์ได้ในตารางด้านล่าง", "success")
    return redirect(url_for("admin.roles") + f"#role-{role_id}")


@bp.route("/roles/<int:role_id>/edit", methods=("POST",))
@admin_required
def role_edit(role_id):
    _row_or_404("roles", role_id)
    name, desc = _role_form()
    if name is not None:
        try:
            get_db().execute("UPDATE roles SET name = ?, description = ? WHERE id = ?", (name, desc, role_id))
            get_db().commit()
            flash(f"บันทึกบทบาท “{name}” แล้ว", "success")
        except sqlite3.IntegrityError:
            flash(f"มีบทบาทชื่อ “{name}” อยู่แล้ว", "danger")
    return redirect(url_for("admin.roles") + f"#role-{role_id}")


@bp.route("/roles/<int:role_id>/delete", methods=("POST",))
@admin_required
def role_delete(role_id):
    role = _row_or_404("roles", role_id)
    db = get_db()
    used = db.execute("SELECT COUNT(*) FROM users WHERE role = ?", (role["code"],)).fetchone()[0]
    if role["is_system"]:
        flash("บทบาทพื้นฐานของระบบลบไม่ได้ (ปรับสิทธิ์ได้)", "danger")
    elif used:
        flash(f"ไม่สามารถลบบทบาท “{role['name']}” ได้ เนื่องจากมีผู้ใช้ {used} คน กรุณาเปลี่ยนบทบาทของผู้ใช้ก่อน", "danger")
    else:
        db.execute("DELETE FROM roles WHERE id = ?", (role_id,))
        db.commit()
        flash(f"ลบบทบาท “{role['name']}” แล้ว", "success")
    return redirect(url_for("admin.roles"))
