import os
import uuid
from datetime import date

from flask import Blueprint, abort, current_app, flash, g, redirect, request, send_from_directory, url_for

from .auth import login_required
from .db import get_db
from .services import can_view_task, get_task, has_perm, is_task_member, notify, notify_supervisors, user_scope

bp = Blueprint("reports", __name__)


def _get_report_or_404(report_id):
    report = get_db().execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        abort(404)
    if not can_view_task(report["task_id"], g.user):
        abort(403)
    return report


def _extension(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@bp.route("/tasks/<int:task_id>/reports", methods=("POST",))
@login_required
def create(task_id):
    task = get_task(task_id)
    if task is None:
        abort(404)
    if not is_task_member(task_id, g.user["id"]):
        abort(403)

    form = request.form
    fields = {k: form.get(k, "").strip() for k in ("result", "problems", "obstacles", "help_needed")}
    report_date = form.get("report_date") or date.today().isoformat()
    try:
        report_date = date.fromisoformat(report_date).isoformat()
    except ValueError:
        flash("รูปแบบวันที่ไม่ถูกต้อง", "danger")
        return redirect(url_for("tasks.detail", task_id=task_id) + "#reports")
    if not any(fields.values()):
        flash("กรุณากรอกผลการดำเนินงาน หรือปัญหา/อุปสรรคอย่างน้อย 1 ช่อง", "danger")
        return redirect(url_for("tasks.detail", task_id=task_id) + "#reports")

    files = [f for f in request.files.getlist("attachments") if f and f.filename]
    allowed = current_app.config["ALLOWED_EXTENSIONS"]
    bad = [f.filename for f in files if _extension(f.filename) not in allowed]
    if bad:
        flash("ไม่รองรับไฟล์ประเภทนี้: " + ", ".join(bad), "danger")
        return redirect(url_for("tasks.detail", task_id=task_id) + "#reports")

    has_issue = bool(fields["problems"] or fields["obstacles"])
    db = get_db()
    cur = db.execute(
        """INSERT INTO reports (task_id, user_id, report_date, result, problems, obstacles, help_needed, issue_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, g.user["id"], report_date, fields["result"], fields["problems"], fields["obstacles"],
         fields["help_needed"], "open" if has_issue else "none"),
    )
    report_id = cur.lastrowid

    folder = current_app.config["UPLOAD_FOLDER"]
    for f in files:
        # Keep the original (possibly Thai) name for display; store under a random safe name
        stored = f"{uuid.uuid4().hex}.{_extension(f.filename)}"
        path = os.path.join(folder, stored)
        f.save(path)
        db.execute(
            "INSERT INTO report_attachments (report_id, original_name, stored_name, size_bytes) VALUES (?, ?, ?, ?)",
            (report_id, os.path.basename(f.filename), stored, os.path.getsize(path)),
        )

    link = url_for("tasks.detail", task_id=task_id) + f"#report-{report_id}"
    if has_issue:
        notify_supervisors(g.user, "issue", f"{g.user['full_name']} รายงานปัญหา/อุปสรรคในงาน “{task['title']}”", link)
    elif user_scope(g.user) != "all":
        notify(task["created_by"], "report", f"{g.user['full_name']} รายงานผลการดำเนินงาน “{task['title']}”", link)
    db.commit()
    flash("บันทึกรายงานเรียบร้อยแล้ว", "success")
    return redirect(link)


@bp.route("/reports/<int:report_id>/comments", methods=("POST",))
@login_required
def comment(report_id):
    report = _get_report_or_404(report_id)
    # Managers / division heads reply; the reporter may answer back in the same thread
    if not (has_perm(g.user, "report.review") or report["user_id"] == g.user["id"]):
        abort(403)
    body = request.form.get("body", "").strip()
    link = url_for("tasks.detail", task_id=report["task_id"]) + f"#report-{report_id}"
    if not body:
        flash("กรุณากรอกข้อความ", "danger")
        return redirect(link)
    db = get_db()
    db.execute("INSERT INTO report_comments (report_id, user_id, body) VALUES (?, ?, ?)",
               (report_id, g.user["id"], body))
    task = get_task(report["task_id"])
    if report["user_id"] != g.user["id"]:
        notify(report["user_id"], "comment", f"{g.user['full_name']} ตอบกลับรายงานของคุณในงาน “{task['title']}”", link)
    else:
        notify_supervisors(g.user, "comment", f"{g.user['full_name']} ตอบกลับในรายงานงาน “{task['title']}”", link)
    db.commit()
    flash("ส่งความคิดเห็นแล้ว", "success")
    return redirect(link)


@bp.route("/reports/<int:report_id>/issue", methods=("POST",))
@login_required
def set_issue_status(report_id):
    report = _get_report_or_404(report_id)  # also checks the task is visible (division heads: own division)
    if not has_perm(g.user, "report.review"):
        abort(403)
    status = request.form.get("issue_status")
    if status not in ("open", "resolved") or report["issue_status"] == "none":
        abort(400)
    db = get_db()
    db.execute("UPDATE reports SET issue_status = ? WHERE id = ?", (status, report_id))
    link = url_for("tasks.detail", task_id=report["task_id"]) + f"#report-{report_id}"
    if status == "resolved":
        task = get_task(report["task_id"])
        notify(report["user_id"], "comment", f"ปัญหาที่คุณรายงานในงาน “{task['title']}” ถูกระบุว่าแก้ไขแล้ว", link)
    db.commit()
    flash("อัปเดตสถานะปัญหาแล้ว", "success")
    return redirect(link)


@bp.route("/attachments/<int:attachment_id>")
@login_required
def download(attachment_id):
    att = get_db().execute(
        """SELECT a.*, r.task_id FROM report_attachments a JOIN reports r ON r.id = a.report_id
           WHERE a.id = ?""", (attachment_id,),
    ).fetchone()
    if att is None:
        abort(404)
    if not can_view_task(att["task_id"], g.user):
        abort(403)
    inline = _extension(att["stored_name"]) in {"pdf", "jpg", "jpeg", "png", "gif"}
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], att["stored_name"],
                               download_name=att["original_name"], as_attachment=not inline)
