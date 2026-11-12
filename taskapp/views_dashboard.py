from datetime import date, timedelta

from flask import Blueprint, g, render_template, request

from .auth import login_required
from .db import get_db
from .services import (STATUS_LABELS, assignable_users, current_academic_year, division_name, due_soon_days,
                       has_perm, query_tasks, user_scope)

bp = Blueprint("dashboard", __name__)


def read_filters(allow_assignee=True):
    f = {k: request.args.get(k, "").strip() for k in ("status", "priority", "date_from", "date_to", "q")}
    if allow_assignee:
        f["assignee"] = request.args.get("assignee", type=int)
    # Academic year: defaults to the current one; "all" shows every year
    year = request.args.get("year", "")
    if year == "all":
        f["year"] = "all"
    elif year.isdigit():
        f["year"] = int(year)
    else:
        current = current_academic_year()
        f["year"] = current["id"] if current else "all"
    return f


@bp.route("/")
@login_required
def index():
    if has_perm(g.user, "dashboard.overview"):
        return manager_dashboard()
    return staff_dashboard()


def manager_dashboard():
    filters = read_filters()
    tasks = query_tasks(g.user, filters)
    db = get_db()

    counts = {key: 0 for key in STATUS_LABELS}
    for t in tasks:
        counts[t["status"]] += 1
    avg_progress = round(sum(t["progress"] for t in tasks) / len(tasks), 1) if tasks else 0
    overdue = [t for t in tasks if t["is_overdue"]]

    task_ids = [t["id"] for t in tasks]
    issues, sub_stats = [], {}
    if task_ids:
        marks = ",".join("?" * len(task_ids))
        issues = db.execute(
            f"""SELECT r.*, t.title AS task_title, u.full_name AS reporter
                FROM reports r JOIN tasks t ON t.id = r.task_id JOIN users u ON u.id = r.user_id
                WHERE r.issue_status = 'open' AND r.task_id IN ({marks})
                ORDER BY r.report_date DESC, r.id DESC""",
            task_ids,
        ).fetchall()
        for r in db.execute(
            f"""SELECT assignee_id, COUNT(*) AS open_subtasks,
                       SUM(CASE WHEN due_date < ? THEN 1 ELSE 0 END) AS overdue_subtasks
                FROM subtasks WHERE status NOT IN ('done', 'on_hold') AND assignee_id IS NOT NULL
                  AND task_id IN ({marks}) GROUP BY assignee_id""",
            [date.today().isoformat(), *task_ids],
        ):
            sub_stats[r["assignee_id"]] = r

    # Workload per team member (whole unit for managers, own division for division heads)
    team = assignable_users(g.user)
    workload = []
    for u in team:
        mine = [t for t in tasks if any(a["id"] == u["id"] for a in t["assignees"])]
        active = [t for t in mine if t["status"] in ("not_started", "in_progress")]
        s = sub_stats.get(u["id"])
        workload.append({
            "user": u,
            "total": len(mine),
            "active": len(active),
            "done": sum(1 for t in mine if t["status"] == "done"),
            "overdue": sum(1 for t in mine if t["is_overdue"]),
            "open_subtasks": s["open_subtasks"] if s else 0,
            "overdue_subtasks": s["overdue_subtasks"] if s else 0,
            "avg_progress": round(sum(t["progress"] for t in active) / len(active), 1) if active else None,
        })
    max_active = max([w["active"] for w in workload] + [1])

    return render_template(
        "dashboard_manager.html",
        filters=filters, tasks=tasks, counts=counts, avg_progress=avg_progress,
        overdue=overdue, issues=issues, workload=workload, max_active=max_active,
        staff=team,
        division_label=(division_name(g.user["division_id"]) if user_scope(g.user) == "division"
                        else "" if user_scope(g.user) == "own" else None),
    )


def staff_dashboard():
    filters = read_filters(allow_assignee=False)
    tasks = query_tasks(g.user, filters)
    today = date.today()
    soon = (today + timedelta(days=due_soon_days())).isoformat()

    open_tasks = [t for t in tasks if t["status"] != "done"]
    due_soon = [t for t in open_tasks if t["status"] != "on_hold" and t["due_date"]
                and today.isoformat() <= t["due_date"] <= soon]
    overdue = [t for t in tasks if t["is_overdue"]]

    my_subtasks = [dict(r) for r in get_db().execute(
        """SELECT s.*, t.title AS task_title FROM subtasks s JOIN tasks t ON t.id = s.task_id
           WHERE s.assignee_id = ? AND s.status != 'done' AND t.status != 'done'
           ORDER BY s.due_date IS NULL, s.due_date""",
        (g.user["id"],),
    )]
    for s in my_subtasks:
        s["is_overdue"] = bool(s["due_date"] and s["due_date"] < today.isoformat() and s["status"] != "on_hold")
        s["is_soon"] = bool(s["due_date"] and today.isoformat() <= s["due_date"] <= soon)

    return render_template(
        "dashboard_staff.html", filters=filters, tasks=tasks, open_tasks=open_tasks,
        due_soon=due_soon, overdue=overdue, my_subtasks=my_subtasks,
    )
