import os
from datetime import date

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .db import get_db
from .services import (PRIORITY_LABELS, STATUS_LABELS, academic_years, assignable_users, bump_task_started,
                       can_edit_subtask, can_manage_task, can_view_task, ensure_academic_year, get_task,
                       can_delegate_task, can_delete_task, has_perm, is_task_member, notify, notify_supervisors,
                       query_tasks, sync_subtask_state, user_scope)
from .views_dashboard import read_filters

bp = Blueprint("tasks", __name__)


# ---------------------------------------------------------------- helpers
def _valid_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return False


def _parse_task_form(form):
    data = {
        "title": form.get("title", "").strip(),
        "description": form.get("description", "").strip(),
        "start_date": _valid_date(form.get("start_date", "").strip()),
        "due_date": _valid_date(form.get("due_date", "").strip()),
        "priority": form.get("priority", "medium"),
        "status": form.get("status", "not_started"),
        "assignees": sorted({int(x) for x in form.getlist("assignees") if x.isdigit()}),
        "academic_year_id": form.get("academic_year_id", type=int),
    }
    errors = []
    if data["academic_year_id"] and data["academic_year_id"] not in {y["id"] for y in academic_years()}:
        errors.append("ปีการศึกษาไม่ถูกต้อง")
    if not data["title"]:
        errors.append("กรุณากรอกชื่องาน")
    if data["start_date"] is False or data["due_date"] is False:
        errors.append("รูปแบบวันที่ไม่ถูกต้อง")
    elif data["start_date"] and data["due_date"] and data["start_date"] > data["due_date"]:
        errors.append("วันเริ่มต้นต้องไม่เกินวันครบกำหนด")
    if data["priority"] not in PRIORITY_LABELS:
        errors.append("ระดับความสำคัญไม่ถูกต้อง")
    if data["status"] not in STATUS_LABELS:
        errors.append("สถานะไม่ถูกต้อง")
    valid_ids = {u["id"] for u in assignable_users(g.user)}
    if not has_perm(g.user, "task.assign"):
        data["assignees"] = []  # users without the assign permission never choose assignees; the views set them
    elif any(a not in valid_ids for a in data["assignees"]):
        errors.append("ผู้รับผิดชอบไม่ถูกต้อง")
    return data, errors


def _resolve_academic_year(data):
    """Chosen year, otherwise the year containing the start (or due) date, otherwise today's year."""
    if data["academic_year_id"]:
        return data["academic_year_id"]
    return ensure_academic_year(data["start_date"] or data["due_date"] or date.today().isoformat())


def _assign(task_id, user_ids, task_title, send_notification=True):
    """Add assignees (ignoring existing ones) and notify the newly assigned users."""
    db = get_db()
    for uid in user_ids:
        cur = db.execute("INSERT OR IGNORE INTO task_assignees (task_id, user_id) VALUES (?, ?)", (task_id, uid))
        if cur.rowcount and send_notification and uid != g.user["id"]:
            notify(uid, "assigned", f"คุณได้รับมอบหมายงานใหม่: “{task_title}”",
                   url_for("tasks.detail", task_id=task_id))


def _load_task_or_404(task_id):
    task = get_task(task_id)
    if task is None:
        abort(404)
    if not can_view_task(task_id, g.user):
        abort(403)
    return task


def _load_managed_task_or_404(task_id):
    """Task the current user may edit: any task for managers, own-created tasks for staff."""
    task = _load_task_or_404(task_id)
    if not can_manage_task(task, g.user):
        abort(403)
    return task


def _get_subtask_or_404(subtask_id):
    sub = get_db().execute("SELECT * FROM subtasks WHERE id = ?", (subtask_id,)).fetchone()
    if sub is None:
        abort(404)
    return sub


# ---------------------------------------------------------------- task CRUD
@bp.route("/tasks")
@login_required
def index():
    filters = read_filters(allow_assignee=has_perm(g.user, "dashboard.overview") or can_delegate_task(g.user))
    tasks = query_tasks(g.user, filters)
    if request.args.get("overdue"):
        tasks = [t for t in tasks if t["is_overdue"]]
    return render_template("tasks_list.html", tasks=tasks, filters=filters, staff=assignable_users(g.user))


@bp.route("/tasks/new", methods=("GET", "POST"))
@login_required
def create():
    if not has_perm(g.user, "task.create"):
        abort(403)
    form_data = {"priority": "medium", "status": "not_started", "assignees": [], "academic_year_id": None}
    if request.method == "POST":
        data, errors = _parse_task_form(request.form)
        if not has_perm(g.user, "task.assign"):
            # Without the assign permission people create work for themselves
            data["assignees"] = [g.user["id"]]
        if errors:
            for e in errors:
                flash(e, "danger")
            form_data = data
        else:
            db = get_db()
            cur = db.execute(
                """INSERT INTO tasks (title, description, start_date, due_date, priority, status, academic_year_id, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (data["title"], data["description"], data["start_date"], data["due_date"],
                 data["priority"], data["status"], _resolve_academic_year(data), g.user["id"]),
            )
            _assign(cur.lastrowid, data["assignees"], data["title"])
            if user_scope(g.user) != "all":
                notify_supervisors(g.user, "new_task", f"{g.user['full_name']} เพิ่มงานใหม่: “{data['title']}”",
                                   url_for("tasks.detail", task_id=cur.lastrowid))
            db.commit()
            flash("สร้างงานเรียบร้อยแล้ว", "success")
            return redirect(url_for("tasks.detail", task_id=cur.lastrowid))
    return render_template("task_form.html", task=form_data, staff=assignable_users(g.user), is_new=True)


@bp.route("/tasks/<int:task_id>/edit", methods=("GET", "POST"))
@login_required
def edit(task_id):
    task = _load_managed_task_or_404(task_id)
    form_data = dict(task, assignees=[a["id"] for a in task["assignees"]])
    if request.method == "POST":
        data, errors = _parse_task_form(request.form)
        if not has_perm(g.user, "task.assign"):
            data["assignees"] = form_data["assignees"]  # no assign permission: assignees stay as they are
        elif user_scope(g.user) != "all":
            # Division-scoped users (re)assign within their division; people from elsewhere stay assigned
            team = {u["id"] for u in assignable_users(g.user)}
            data["assignees"] = sorted(set(data["assignees"]) | (set(form_data["assignees"]) - team))
        if errors:
            for e in errors:
                flash(e, "danger")
            form_data = dict(task, **data)
        else:
            db = get_db()
            manual = 100 if data["status"] == "done" and task["subtask_count"] == 0 else task["manual_progress"]
            db.execute(
                """UPDATE tasks SET title = ?, description = ?, start_date = ?, due_date = ?, priority = ?,
                   status = ?, manual_progress = ?, academic_year_id = ?, updated_at = datetime('now','localtime')
                   WHERE id = ?""",
                (data["title"], data["description"], data["start_date"], data["due_date"], data["priority"],
                 data["status"], manual, _resolve_academic_year(data), task_id),
            )
            old = {a["id"] for a in task["assignees"]}
            removed = old - set(data["assignees"])
            if removed:
                marks = ",".join("?" * len(removed))
                db.execute(f"DELETE FROM task_assignees WHERE task_id = ? AND user_id IN ({marks})",
                           (task_id, *removed))
            _assign(task_id, data["assignees"], data["title"])
            db.commit()
            flash("บันทึกการแก้ไขเรียบร้อยแล้ว", "success")
            return redirect(url_for("tasks.detail", task_id=task_id))
    return render_template("task_form.html", task=form_data, staff=assignable_users(g.user), is_new=False,
                           external_assignees=[a for a in task["assignees"]
                                               if a["id"] not in {u["id"] for u in assignable_users(g.user)}])


@bp.route("/tasks/<int:task_id>/delete", methods=("POST",))
@login_required
def delete(task_id):
    task = _load_task_or_404(task_id)
    if not can_delete_task(task, g.user):
        abort(403)
    db = get_db()
    files = db.execute(
        """SELECT a.stored_name FROM report_attachments a JOIN reports r ON r.id = a.report_id
           WHERE r.task_id = ?""", (task_id,),
    ).fetchall()
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    for f in files:
        try:
            os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], f["stored_name"]))
        except OSError:
            pass
    flash(f"ลบงาน “{task['title']}” แล้ว", "success")
    return redirect(url_for("tasks.index"))


@bp.route("/tasks/<int:task_id>/delegate", methods=("POST",))
@login_required
def delegate(task_id):
    """Assign / unassign people from the user's own scope (e.g. a division head handing a task from the
    unit manager to team members). Assignees outside that scope are never touched."""
    task = _load_task_or_404(task_id)
    if not can_delegate_task(g.user):
        abort(403)
    team = {u["id"] for u in assignable_users(g.user)}
    chosen = {int(x) for x in request.form.getlist("assignees") if x.isdigit()}
    if chosen - team:
        flash("มอบหมายได้เฉพาะผู้ใช้ในขอบเขตของคุณ", "danger")
        return redirect(url_for("tasks.detail", task_id=task_id))
    old = {a["id"] for a in task["assignees"]}
    removed = (old & team) - chosen
    added = chosen - old
    db = get_db()
    if removed:
        marks = ",".join("?" * len(removed))
        db.execute(f"DELETE FROM task_assignees WHERE task_id = ? AND user_id IN ({marks})", (task_id, *removed))
    _assign(task_id, sorted(added), task["title"])
    db.commit()
    if added or removed:
        flash(f"มอบหมายงานแล้ว (เพิ่ม {len(added)} คน, นำออก {len(removed)} คน)", "success")
    else:
        flash("ไม่มีการเปลี่ยนแปลงผู้รับผิดชอบ", "info")
    # Removing yourself may hide the task from you
    if can_view_task(task_id, g.user):
        return redirect(url_for("tasks.detail", task_id=task_id))
    return redirect(url_for("tasks.index"))


@bp.route("/tasks/<int:task_id>")
@login_required
def detail(task_id):
    task = _load_task_or_404(task_id)
    db = get_db()
    today = date.today().isoformat()
    subtasks = [dict(r) for r in db.execute(
        """SELECT s.*, u.full_name AS assignee_name FROM subtasks s
           LEFT JOIN users u ON u.id = s.assignee_id WHERE s.task_id = ? ORDER BY s.due_date IS NULL, s.due_date, s.id""",
        (task_id,),
    )]
    for s in subtasks:
        s["is_overdue"] = bool(s["due_date"] and s["due_date"] < today and s["status"] not in ("done", "on_hold"))
        s["can_edit"] = can_edit_subtask(s, g.user, task)

    reports = [dict(r) for r in db.execute(
        """SELECT r.*, u.full_name AS reporter FROM reports r JOIN users u ON u.id = r.user_id
           WHERE r.task_id = ? ORDER BY r.report_date DESC, r.id DESC""", (task_id,),
    )]
    report_ids = [r["id"] for r in reports]
    attachments, comments = {}, {}
    if report_ids:
        marks = ",".join("?" * len(report_ids))
        for a in db.execute(f"SELECT * FROM report_attachments WHERE report_id IN ({marks}) ORDER BY id", report_ids):
            attachments.setdefault(a["report_id"], []).append(a)
        for c in db.execute(
            f"""SELECT c.*, u.full_name, u.role FROM report_comments c JOIN users u ON u.id = c.user_id
                WHERE c.report_id IN ({marks}) ORDER BY c.created_at, c.id""", report_ids,
        ):
            comments.setdefault(c["report_id"], []).append(c)

    member = is_task_member(task_id, g.user["id"])
    can_manage = can_manage_task(task, g.user)
    team = assignable_users(g.user)
    team_ids = {u["id"] for u in team}
    return render_template(
        "task_detail.html", task=task, subtasks=subtasks, reports=reports,
        attachments=attachments, comments=comments,
        weight_total=round(sum(s["weight"] for s in subtasks), 2),
        # Staff owners may only give sub-tasks to themselves
        staff=assignable_users(g.user),
        can_manage=can_manage, can_delete=can_delete_task(task, g.user), can_report=member,
        can_set_progress=task["subtask_count"] == 0 and (can_manage or member),
        # Delegation: hand a visible task (e.g. one assigned by the unit manager) to people in your own scope
        can_delegate=can_delegate_task(g.user) and not (can_manage and user_scope(g.user) == "all"),
        delegate_team=team, delegate_selected={a["id"] for a in task["assignees"]} & team_ids,
        other_assignees=[a for a in task["assignees"] if a["id"] not in team_ids],
        allowed_ext=sorted(current_app.config["ALLOWED_EXTENSIONS"]),
    )


@bp.route("/tasks/<int:task_id>/progress", methods=("POST",))
@login_required
def set_progress(task_id):
    """Manual progress, only used while a task has no sub-tasks."""
    task = _load_task_or_404(task_id)
    if not (can_manage_task(task, g.user) or is_task_member(task_id, g.user["id"])):
        abort(403)
    if task["subtask_count"] > 0:
        flash("งานนี้มีงานย่อย ความคืบหน้าจะคำนวณอัตโนมัติจากงานย่อย", "warning")
        return redirect(url_for("tasks.detail", task_id=task_id))
    try:
        value = max(0.0, min(100.0, float(request.form.get("progress", 0))))
    except ValueError:
        abort(400)
    get_db().execute("UPDATE tasks SET manual_progress = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                     (value, task_id))
    bump_task_started(task_id)
    get_db().commit()
    flash(f"อัปเดตความคืบหน้าเป็น {value:.0f}% แล้ว", "success")
    return redirect(url_for("tasks.detail", task_id=task_id))


@bp.route("/tasks/<int:task_id>/status", methods=("POST",))
@login_required
def set_status(task_id):
    task = _load_managed_task_or_404(task_id)
    status = request.form.get("status")
    if status not in STATUS_LABELS:
        abort(400)
    db = get_db()
    db.execute("UPDATE tasks SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?", (status, task_id))
    if status == "done" and task["subtask_count"] == 0:
        db.execute("UPDATE tasks SET manual_progress = 100 WHERE id = ?", (task_id,))
    db.commit()
    flash(f"เปลี่ยนสถานะเป็น “{STATUS_LABELS[status]}” แล้ว", "success")
    return redirect(url_for("tasks.detail", task_id=task_id))


# ---------------------------------------------------------------- sub-tasks
def weight_used(task_id, exclude_subtask_id=None):
    """Sum of sub-task weights (%) already allocated in a task, optionally ignoring one sub-task."""
    row = get_db().execute("SELECT COALESCE(SUM(weight), 0) FROM subtasks WHERE task_id = ? AND id != ?",
                           (task_id, exclude_subtask_id or 0)).fetchone()
    return round(row[0], 2)


def _fmt_pct(v):
    return f"{v:g}"


def _parse_subtask_form(form, task_id, exclude_subtask_id=None):
    title = form.get("title", "").strip()
    due = _valid_date(form.get("due_date", "").strip())
    assignee = form.get("assignee_id", type=int)
    remaining = round(100 - weight_used(task_id, exclude_subtask_id), 2)
    raw_weight = (form.get("weight") or "").strip()
    try:
        weight = round(float(raw_weight), 1) if raw_weight else remaining
    except ValueError:
        weight = None
    errors = []
    if not title:
        errors.append("กรุณากรอกชื่องานย่อย")
    if due is False:
        errors.append("รูปแบบวันที่ไม่ถูกต้อง")
    if remaining <= 0:
        errors.append("น้ำหนักของงานย่อยรวมครบ 100% แล้ว — กรุณาลดน้ำหนักงานย่อยอื่นก่อน")
    elif weight is None or not 0 < weight <= 100:
        errors.append("น้ำหนักต้องเป็นตัวเลขมากกว่า 0 และไม่เกิน 100%")
    elif weight > remaining:
        errors.append(f"น้ำหนักรวมของงานย่อยต้องไม่เกิน 100% — ใช้ไปแล้ว {_fmt_pct(100 - remaining)}% "
                      f"กำหนดได้อีกไม่เกิน {_fmt_pct(remaining)}%")
    allowed = {u["id"] for u in assignable_users(g.user)}
    if assignee and assignee not in allowed:
        errors.append("ผู้รับผิดชอบไม่ถูกต้อง")
    return {"title": title, "due_date": due or None, "assignee_id": assignee or None, "weight": weight}, errors


@bp.route("/tasks/<int:task_id>/subtasks", methods=("POST",))
@login_required
def add_subtask(task_id):
    task = _load_managed_task_or_404(task_id)
    data, errors = _parse_subtask_form(request.form, task_id)
    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("tasks.detail", task_id=task_id) + "#subtasks")
    db = get_db()
    db.execute(
        "INSERT INTO subtasks (task_id, title, assignee_id, due_date, weight) VALUES (?, ?, ?, ?, ?)",
        (task_id, data["title"], data["assignee_id"], data["due_date"], data["weight"]),
    )
    if data["assignee_id"]:
        # A sub-task owner automatically becomes a task assignee
        _assign(task_id, [data["assignee_id"]], task["title"], send_notification=False)
        if data["assignee_id"] != g.user["id"]:
            notify(data["assignee_id"], "assigned", f"คุณได้รับมอบหมายงานย่อย “{data['title']}” ในงาน “{task['title']}”",
                   url_for("tasks.detail", task_id=task_id))
    db.commit()
    flash("เพิ่มงานย่อยแล้ว", "success")
    return redirect(url_for("tasks.detail", task_id=task_id) + "#subtasks")


@bp.route("/subtasks/<int:subtask_id>/edit", methods=("POST",))
@login_required
def edit_subtask(subtask_id):
    sub = _get_subtask_or_404(subtask_id)
    task = _load_managed_task_or_404(sub["task_id"])
    data, errors = _parse_subtask_form(request.form, task["id"], subtask_id)
    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("tasks.detail", task_id=task["id"]) + "#subtasks")
    db = get_db()
    db.execute(
        """UPDATE subtasks SET title = ?, assignee_id = ?, due_date = ?, weight = ?,
           updated_at = datetime('now','localtime') WHERE id = ?""",
        (data["title"], data["assignee_id"], data["due_date"], data["weight"], subtask_id),
    )
    if data["assignee_id"] and data["assignee_id"] != sub["assignee_id"]:
        _assign(task["id"], [data["assignee_id"]], task["title"], send_notification=False)
    if data["assignee_id"] and data["assignee_id"] not in (sub["assignee_id"], g.user["id"]):
        notify(data["assignee_id"], "assigned", f"คุณได้รับมอบหมายงานย่อย “{data['title']}” ในงาน “{task['title']}”",
               url_for("tasks.detail", task_id=task["id"]))
    db.commit()
    flash("แก้ไขงานย่อยแล้ว", "success")
    return redirect(url_for("tasks.detail", task_id=task["id"]) + "#subtasks")


@bp.route("/subtasks/<int:subtask_id>/update", methods=("POST",))
@login_required
def update_subtask(subtask_id):
    """Status / progress update by the sub-task owner (or a manager)."""
    sub = _get_subtask_or_404(subtask_id)
    if not can_edit_subtask(sub, g.user, get_task(sub["task_id"])):
        abort(403)
    status = request.form.get("status", sub["status"])
    if status not in STATUS_LABELS:
        abort(400)
    try:
        progress = float(request.form.get("progress", sub["progress"]))
    except ValueError:
        abort(400)
    status, progress = sync_subtask_state(status, progress)
    db = get_db()
    db.execute("UPDATE subtasks SET status = ?, progress = ?, updated_at = datetime('now','localtime') WHERE id = ?",
               (status, progress, subtask_id))
    bump_task_started(sub["task_id"])
    db.commit()
    flash(f"อัปเดตงานย่อย “{sub['title']}” แล้ว ({progress:.0f}%)", "success")
    nxt = request.form.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(url_for("tasks.detail", task_id=sub["task_id"]) + "#subtasks")


@bp.route("/subtasks/<int:subtask_id>/delete", methods=("POST",))
@login_required
def delete_subtask(subtask_id):
    sub = _get_subtask_or_404(subtask_id)
    _load_managed_task_or_404(sub["task_id"])
    get_db().execute("DELETE FROM subtasks WHERE id = ?", (subtask_id,))
    get_db().commit()
    flash(f"ลบงานย่อย “{sub['title']}” แล้ว", "success")
    return redirect(url_for("tasks.detail", task_id=sub["task_id"]) + "#subtasks")
