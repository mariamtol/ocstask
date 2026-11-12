"""Business logic: progress calculation, access control, task queries and notifications."""
from datetime import date, timedelta

from flask import url_for

from flask import g

from .db import academic_year_bounds, academic_year_of, get_db
from .permissions import ADMIN_PERMISSIONS, ADMIN_ROLE, PERMISSIONS, SCOPE_LABELS  # noqa: F401 (re-exported)

DEFAULT_SETTINGS = {"due_soon_days": "3", "org_name": "หน่วยงาน"}

STATUS_LABELS = {
    "not_started": "ยังไม่เริ่ม",
    "in_progress": "กำลังดำเนินการ",
    "done": "เสร็จสิ้น",
    "on_hold": "ระงับ",
}
PRIORITY_LABELS = {"low": "ต่ำ", "medium": "กลาง", "high": "สูง", "urgent": "เร่งด่วน"}
# Weighted progress per task = Σ(progress × weight) / Σ(weight); falls back to manual_progress
TASK_SELECT = """
SELECT t.*, u.full_name AS creator_name, u.role AS creator_role, u.division_id AS creator_division_id,
       COALESCE(cr.scope, 'own') AS creator_scope, ay.year_be AS academic_year,
       COALESCE(st.cnt, 0) AS subtask_count,
       COALESCE(st.done_cnt, 0) AS subtask_done,
       CASE WHEN COALESCE(st.cnt, 0) > 0 THEN ROUND(st.wp / st.tw, 1)
            ELSE t.manual_progress END AS progress,
       (SELECT COUNT(*) FROM reports r WHERE r.task_id = t.id AND r.issue_status = 'open') AS open_issues
FROM tasks t
JOIN users u ON u.id = t.created_by
LEFT JOIN roles cr ON cr.code = u.role
LEFT JOIN academic_years ay ON ay.id = t.academic_year_id
LEFT JOIN (
    SELECT task_id, COUNT(*) AS cnt, SUM(progress * weight) AS wp, SUM(weight) AS tw,
           SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done_cnt
    FROM subtasks GROUP BY task_id
) st ON st.task_id = t.id
"""

OVERDUE_SQL = "(t.due_date IS NOT NULL AND t.due_date < :today AND t.status NOT IN ('done', 'on_hold'))"


def today_iso():
    return date.today().isoformat()


# ---------------------------------------------------------------- settings
def get_setting(key):
    if "settings" not in g:
        g.settings = dict(DEFAULT_SETTINGS)
        g.settings.update({r["key"]: r["value"] for r in get_db().execute("SELECT key, value FROM settings")})
    return g.settings.get(key, DEFAULT_SETTINGS.get(key))


def set_setting(key, value):
    get_db().execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, str(value)))
    g.pop("settings", None)


def due_soon_days():
    try:
        return max(0, int(get_setting("due_soon_days")))
    except ValueError:
        return 3


# ---------------------------------------------------------------- academic years
def academic_years():
    if "academic_years" not in g:
        g.academic_years = get_db().execute("SELECT * FROM academic_years ORDER BY year_be DESC").fetchall()
    return g.academic_years


def current_academic_year():
    """The year flagged as current by the admin, else the one containing today, else the latest."""
    years = academic_years()
    today = today_iso()
    return (next((y for y in years if y["is_current"]), None)
            or next((y for y in years if y["start_date"] <= today <= y["end_date"]), None)
            or (years[0] if years else None))


def academic_year_for_date(iso_date):
    if not iso_date:
        return None
    return next((y for y in academic_years() if y["start_date"] <= iso_date <= y["end_date"]), None)


def ensure_academic_year(iso_date):
    """Find the academic year containing a date, creating it with default bounds if missing."""
    year = academic_year_for_date(iso_date)
    if year is not None:
        return year["id"]
    year_be = academic_year_of(date.fromisoformat(iso_date))
    db = get_db()
    db.execute("INSERT OR IGNORE INTO academic_years (year_be, start_date, end_date) VALUES (?, ?, ?)",
               (year_be, *academic_year_bounds(year_be)))
    g.pop("academic_years", None)
    return db.execute("SELECT id FROM academic_years WHERE year_be = ?", (year_be,)).fetchone()["id"]


# ---------------------------------------------------------------- roles & access control
def roles():
    """All roles (code -> dict with name, scope, perms), cached per request."""
    if "roles" not in g:
        db = get_db()
        perms = {}
        for r in db.execute("SELECT role_id, permission FROM role_permissions"):
            perms.setdefault(r["role_id"], set()).add(r["permission"])
        g.roles = {}
        for r in db.execute("SELECT * FROM roles ORDER BY sort_order, id"):
            info = dict(r)
            info["perms"] = set(ADMIN_PERMISSIONS) if r["code"] == ADMIN_ROLE else perms.get(r["id"], set())
            g.roles[r["code"]] = info
    return g.roles


def role_labels():
    return {code: r["name"] for code, r in roles().items()}


def has_perm(user, perm):
    if user is None:
        return False
    role = roles().get(user["role"])
    return role is not None and perm in role["perms"]


def user_scope(user):
    """'all' | 'division' | 'own'. A division-scoped user without a division falls back to 'own'."""
    role = roles().get(user["role"]) if user is not None else None
    scope = role["scope"] if role else "own"
    if scope == "division" and user["division_id"] is None:
        return "own"
    return scope


def is_admin(user):
    return user is not None and user["role"] == ADMIN_ROLE


def is_manager(user):
    """Organisation-wide supervisor: scope 'all' and allowed to manage others' tasks."""
    return user is not None and user_scope(user) == "all" and has_perm(user, "task.manage")


def _receiver_roles():
    return [code for code, r in roles().items() if "task.receive" in r["perms"]]


def assignable_users(user):
    """Who `user` may assign tasks / sub-tasks to (people whose role can receive tasks, within scope)."""
    if not has_perm(user, "task.assign"):
        return [user]
    scope = user_scope(user)
    if scope == "own":
        return [user]
    codes = _receiver_roles()
    if not codes:
        return []
    marks = ",".join("?" * len(codes))
    sql = f"SELECT * FROM users WHERE is_active = 1 AND role IN ({marks})"
    args = list(codes)
    if scope == "division":
        sql += " AND division_id = ?"
        args.append(user["division_id"])
    return get_db().execute(sql + " ORDER BY full_name", args).fetchall()


def division_name(division_id):
    row = get_db().execute(
        """SELECT v.name, d.name AS department FROM divisions v JOIN departments d ON d.id = v.department_id
           WHERE v.id = ?""", (division_id,)).fetchone()
    return f"{row['name']} ({row['department']})" if row else ""


def visibility_clause(user, params):
    """SQL condition (on alias t) limiting tasks to those `user` may see; adds its parameters to `params`."""
    scope = user_scope(user)
    if scope == "all":
        return "1 = 1"
    params["me"] = user["id"]
    clauses = [
        "t.created_by = :me",
        "EXISTS (SELECT 1 FROM task_assignees ta WHERE ta.task_id = t.id AND ta.user_id = :me)",
        "EXISTS (SELECT 1 FROM subtasks s WHERE s.task_id = t.id AND s.assignee_id = :me)",
    ]
    if scope == "division":
        # ... plus every task that involves a member of the division
        params["my_div"] = user["division_id"]
        clauses += [
            """EXISTS (SELECT 1 FROM task_assignees ta3 JOIN users u3 ON u3.id = ta3.user_id
                       WHERE ta3.task_id = t.id AND u3.division_id = :my_div)""",
            """EXISTS (SELECT 1 FROM subtasks s3 JOIN users u4 ON u4.id = s3.assignee_id
                       WHERE s3.task_id = t.id AND u4.division_id = :my_div)""",
            """EXISTS (SELECT 1 FROM users u5 JOIN roles r5 ON r5.code = u5.role
                       WHERE u5.id = t.created_by AND u5.division_id = :my_div AND r5.scope != 'all')""",
        ]
    return "(" + " OR ".join(clauses) + ")"


def is_task_member(task_id, user_id):
    row = get_db().execute(
        """SELECT 1 FROM task_assignees WHERE task_id = ? AND user_id = ?
           UNION SELECT 1 FROM subtasks WHERE task_id = ? AND assignee_id = ?""",
        (task_id, user_id, task_id, user_id),
    ).fetchone()
    return row is not None


def can_view_task(task_id, user):
    if user_scope(user) == "all":
        return True
    params = {"tid": task_id}
    clause = visibility_clause(user, params)
    return get_db().execute(f"SELECT 1 FROM tasks t WHERE t.id = :tid AND {clause}", params).fetchone() is not None


def _task_in_scope(task, user):
    """Is the task's creator inside `user`'s scope? (division scope never reaches org-wide supervisors' tasks)"""
    scope = user_scope(user)
    if scope == "all":
        return True
    if scope == "division":
        return task["creator_division_id"] == user["division_id"] and task["creator_scope"] != "all"
    return False


def can_manage_task(task, user):
    """Creators always manage their own tasks; others need 'task.manage' and the task inside their scope."""
    if task["created_by"] == user["id"]:
        return True
    return has_perm(user, "task.manage") and _task_in_scope(task, user)


def can_delete_task(task, user):
    if task["created_by"] == user["id"]:
        return True
    return has_perm(user, "task.delete") and _task_in_scope(task, user)


def can_delegate_task(user):
    """May (re)assign a visible task to people in their own scope, without full edit rights."""
    return has_perm(user, "task.assign") and user_scope(user) != "own"


def can_edit_subtask(subtask, user, task=None):
    return (subtask["assignee_id"] == user["id"]
            or (task is not None and can_manage_task(task, user)))


# ---------------------------------------------------------------- queries
def query_tasks(user, filters=None, extra_where=None, order="t.due_date IS NULL, t.due_date, t.id"):
    """Return tasks visible to `user`, narrowed by dashboard/list filters."""
    filters = filters or {}
    where, params = [], {"today": today_iso()}

    if user_scope(user) != "all":
        where.append(visibility_clause(user, params))
    if filters.get("year") and filters["year"] != "all":
        where.append("t.academic_year_id = :year")
        params["year"] = filters["year"]
    if filters.get("status"):
        where.append("t.status = :status")
        params["status"] = filters["status"]
    if filters.get("priority"):
        where.append("t.priority = :priority")
        params["priority"] = filters["priority"]
    if filters.get("assignee"):
        where.append("EXISTS (SELECT 1 FROM task_assignees ta2 WHERE ta2.task_id = t.id AND ta2.user_id = :assignee)")
        params["assignee"] = filters["assignee"]
    # Date range: tasks whose [start, due] period overlaps [date_from, date_to]
    if filters.get("date_from"):
        where.append("COALESCE(t.due_date, t.start_date) >= :date_from")
        params["date_from"] = filters["date_from"]
    if filters.get("date_to"):
        where.append("COALESCE(t.start_date, t.due_date) <= :date_to")
        params["date_to"] = filters["date_to"]
    if filters.get("q"):
        where.append("(t.title LIKE :q OR t.description LIKE :q)")
        params["q"] = f"%{filters['q']}%"
    if extra_where:
        where.append(extra_where)

    sql = TASK_SELECT + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order}"
    rows = [dict(r) for r in get_db().execute(sql, params).fetchall()]
    attach_assignees(rows)
    return rows


def get_task(task_id):
    row = get_db().execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    task = dict(row)
    attach_assignees([task])
    return task


def attach_assignees(tasks):
    if not tasks:
        return
    ids = [t["id"] for t in tasks]
    marks = ",".join("?" * len(ids))
    rows = get_db().execute(
        f"""SELECT ta.task_id, u.id, u.full_name FROM task_assignees ta
            JOIN users u ON u.id = ta.user_id WHERE ta.task_id IN ({marks}) ORDER BY u.full_name""",
        ids,
    ).fetchall()
    by_task = {}
    for r in rows:
        by_task.setdefault(r["task_id"], []).append({"id": r["id"], "full_name": r["full_name"]})
    today = today_iso()
    for t in tasks:
        t["assignees"] = by_task.get(t["id"], [])
        t["is_overdue"] = bool(t["due_date"] and t["due_date"] < today and t["status"] not in ("done", "on_hold"))
        t["days_left"] = (date.fromisoformat(t["due_date"]) - date.today()).days if t["due_date"] else None


def active_users(role=None):
    sql = "SELECT * FROM users WHERE is_active = 1"
    params = ()
    if role:
        sql += " AND role = ?"
        params = (role,)
    return get_db().execute(sql + " ORDER BY full_name", params).fetchall()


# ---------------------------------------------------------------- progress
def sync_subtask_state(status, progress):
    """Keep sub-task status and percentage consistent with each other."""
    progress = max(0.0, min(100.0, float(progress)))
    if status == "done":
        progress = 100.0
    elif progress >= 100:
        status = "done"
    elif status == "not_started" and progress > 0:
        status = "in_progress"
    return status, progress


def bump_task_started(task_id):
    """A task that has any progress is no longer 'not started'."""
    task = get_task(task_id)
    if task and task["status"] == "not_started" and task["progress"] > 0:
        get_db().execute(
            "UPDATE tasks SET status = 'in_progress', updated_at = datetime('now','localtime') WHERE id = ?",
            (task_id,),
        )


# ---------------------------------------------------------------- notifications
def notify(user_id, ntype, message, link=None, dedupe_key=None):
    get_db().execute(
        "INSERT OR IGNORE INTO notifications (user_id, type, message, link, dedupe_key) VALUES (?, ?, ?, ?, ?)",
        (user_id, ntype, message, link, dedupe_key),
    )


def notify_supervisors(actor, ntype, message, link=None):
    """Notify everyone whose role has 'notify.supervisor' and whose scope covers `actor`
    (org-wide supervisors always; division-scoped ones only for their own division). Never the actor."""
    codes = [code for code, r in roles().items() if "notify.supervisor" in r["perms"] and r["scope"] != "own"]
    ids = set()
    for u in active_users():
        if u["role"] not in codes or u["id"] == actor["id"]:
            continue
        scope = user_scope(u)
        if scope == "all" or (scope == "division" and u["division_id"] == actor["division_id"]):
            ids.add(u["id"])
    for uid in sorted(ids):
        notify(uid, ntype, message, link)


def sweep_due_notifications():
    """Create (deduplicated) 'due soon' / 'overdue' notifications for tasks and sub-tasks."""
    db = get_db()
    today = date.today()
    soon = (today + timedelta(days=due_soon_days())).isoformat()

    tasks = db.execute(
        """SELECT id, title, due_date, created_by FROM tasks
           WHERE status NOT IN ('done', 'on_hold') AND due_date IS NOT NULL AND due_date <= ?""",
        (soon,),
    ).fetchall()
    for t in tasks:
        link = url_for("tasks.detail", task_id=t["id"])
        overdue = t["due_date"] < today.isoformat()
        kind = "overdue" if overdue else "due_soon"
        recipients = {r["user_id"] for r in db.execute("SELECT user_id FROM task_assignees WHERE task_id = ?", (t["id"],))}
        if overdue:
            recipients.add(t["created_by"])
            msg = f"งาน “{t['title']}” เลยกำหนดแล้ว (ครบกำหนด {t['due_date']})"
        else:
            days = (date.fromisoformat(t["due_date"]) - today).days
            msg = f"งาน “{t['title']}” ใกล้ครบกำหนด" + (" (วันนี้)" if days == 0 else f" (อีก {days} วัน)")
        for uid in recipients:
            notify(uid, kind, msg, link, f"{kind}:task:{t['id']}:{t['due_date']}:{uid}")

    subtasks = db.execute(
        """SELECT s.id, s.title, s.due_date, s.assignee_id, s.task_id, t.title AS task_title
           FROM subtasks s JOIN tasks t ON t.id = s.task_id
           WHERE s.status NOT IN ('done', 'on_hold') AND t.status NOT IN ('done', 'on_hold')
             AND s.assignee_id IS NOT NULL AND s.due_date IS NOT NULL AND s.due_date <= ?""",
        (soon,),
    ).fetchall()
    for s in subtasks:
        link = url_for("tasks.detail", task_id=s["task_id"])
        overdue = s["due_date"] < today.isoformat()
        kind = "overdue" if overdue else "due_soon"
        if overdue:
            msg = f"งานย่อย “{s['title']}” ({s['task_title']}) เลยกำหนดแล้ว"
        else:
            days = (date.fromisoformat(s["due_date"]) - today).days
            msg = f"งานย่อย “{s['title']}” ({s['task_title']}) ใกล้ครบกำหนด" + (" (วันนี้)" if days == 0 else f" (อีก {days} วัน)")
        notify(s["assignee_id"], kind, msg, link, f"{kind}:subtask:{s['id']}:{s['due_date']}:{s['assignee_id']}")
    db.commit()


def unread_count(user_id):
    return get_db().execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0", (user_id,)
    ).fetchone()[0]
