import secrets
import sqlite3
from datetime import date
from pathlib import Path

from flask import current_app, g
from werkzeug.security import generate_password_hash

from .permissions import seed_roles


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    db.executescript(schema)
    seed_roles(db)
    db.commit()


def academic_year_bounds(year_be):
    """ปีการศึกษา พ.ศ. X = 1 ส.ค. (X-543) ถึง 31 ก.ค. (X-542)"""
    ce = year_be - 543
    return date(ce, 8, 1).isoformat(), date(ce + 1, 7, 31).isoformat()


def academic_year_of(day):
    """Buddhist-era academic year that contains `day` (a date)."""
    return (day.year if day.month >= 8 else day.year - 1) + 543


def convert_weights_to_percent(db):
    """Rescale each task's relative sub-task weights to whole percentages summing to exactly 100
    (largest-remainder rounding), so the weighted progress of every task stays the same."""
    task_ids = [r[0] for r in db.execute("SELECT DISTINCT task_id FROM subtasks")]
    for task_id in task_ids:
        subs = db.execute("SELECT id, weight FROM subtasks WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        total = sum(s[1] for s in subs)
        if len(subs) > 100 or total <= 0:
            continue
        raw = [(s[0], s[1] * 100 / total) for s in subs]
        pct = {sid: max(1, int(v)) for sid, v in raw}
        leftover = 100 - sum(pct.values())
        for sid, v in sorted(raw, key=lambda x: x[1] - int(x[1]), reverse=True):
            if leftover <= 0:
                break
            pct[sid] += 1
            leftover -= 1
        while leftover < 0:  # only when max(1, …) pushed the sum over 100
            sid = max(pct, key=pct.get)
            pct[sid] -= 1
            leftover += 1
        for sid, p in pct.items():
            db.execute("UPDATE subtasks SET weight = ? WHERE id = ?", (p, sid))


def migrate_db():
    """Upgrade a database created by an older version without losing data."""
    db = sqlite3.connect(current_app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    try:
        users_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        if users_sql is None:
            return  # empty database: init_db/seed will create everything

        # 1) Configurable roles: roles + role_permissions tables, users.role references roles(code)
        #    (SQLite cannot drop a CHECK constraint -> rebuild users, keeping every existing column)
        db.executescript("""
            CREATE TABLE IF NOT EXISTS roles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                name        TEXT    NOT NULL UNIQUE,
                description TEXT,
                scope       TEXT    NOT NULL DEFAULT 'own' CHECK (scope IN ('all', 'division', 'own')),
                is_system   INTEGER NOT NULL DEFAULT 0,
                sort_order  INTEGER NOT NULL DEFAULT 100,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS role_permissions (
                role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                permission TEXT    NOT NULL,
                PRIMARY KEY (role_id, permission)
            );
        """)
        seed_roles(db)
        db.commit()
        if "REFERENCES roles" not in users_sql["sql"]:
            old_cols = [r["name"] for r in db.execute("PRAGMA table_info(users)")]
            new_cols = ["id", "username", "password_hash", "full_name", "position", "role", "is_active",
                        "created_at", "department_id", "division_id"]
            cols = ", ".join(c for c in new_cols if c in old_cols)
            db.execute("PRAGMA foreign_keys = OFF")
            db.executescript(f"""
                BEGIN;
                CREATE TABLE users_new (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT    NOT NULL UNIQUE,
                    password_hash TEXT    NOT NULL,
                    full_name     TEXT    NOT NULL,
                    position      TEXT,
                    role          TEXT    NOT NULL REFERENCES roles(code) ON UPDATE CASCADE,
                    is_active     INTEGER NOT NULL DEFAULT 1,
                    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                    division_id   INTEGER REFERENCES divisions(id) ON DELETE SET NULL
                );
                INSERT INTO users_new ({cols}) SELECT {cols} FROM users;
                DROP TABLE users;
                ALTER TABLE users_new RENAME TO users;
                COMMIT;
            """)
            db.execute("PRAGMA foreign_keys = ON")

        # 2) New tables
        db.executescript("""
            CREATE TABLE IF NOT EXISTS academic_years (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                year_be    INTEGER NOT NULL UNIQUE,
                start_date TEXT    NOT NULL,
                end_date   TEXT    NOT NULL,
                is_current INTEGER NOT NULL DEFAULT 0,
                created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                CHECK (start_date < end_date)
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS departments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL UNIQUE,
                created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS divisions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
                name          TEXT    NOT NULL,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE (department_id, name)
            );
        """)
        user_cols = {r["name"] for r in db.execute("PRAGMA table_info(users)")}
        if "department_id" not in user_cols:
            db.execute("ALTER TABLE users ADD COLUMN department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL")
        if "division_id" not in user_cols:
            db.execute("ALTER TABLE users ADD COLUMN division_id INTEGER REFERENCES divisions(id) ON DELETE SET NULL")

        # 3) Link tasks to an academic year, derived from each task's start (or due/created) date
        cols = {r["name"] for r in db.execute("PRAGMA table_info(tasks)")}
        if "academic_year_id" not in cols:
            db.execute("ALTER TABLE tasks ADD COLUMN academic_year_id INTEGER REFERENCES academic_years(id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_year ON tasks(academic_year_id)")
        for t in db.execute("SELECT id, COALESCE(start_date, due_date, substr(created_at, 1, 10)) AS d "
                            "FROM tasks WHERE academic_year_id IS NULL").fetchall():
            year_be = academic_year_of(date.fromisoformat(t["d"]))
            db.execute("INSERT OR IGNORE INTO academic_years (year_be, start_date, end_date) VALUES (?, ?, ?)",
                       (year_be, *academic_year_bounds(year_be)))
            yid = db.execute("SELECT id FROM academic_years WHERE year_be = ?", (year_be,)).fetchone()["id"]
            db.execute("UPDATE tasks SET academic_year_id = ? WHERE id = ?", (yid, t["id"]))

        # 4) Make sure the current academic year exists and is marked
        year_be = academic_year_of(date.today())
        db.execute("INSERT OR IGNORE INTO academic_years (year_be, start_date, end_date) VALUES (?, ?, ?)",
                   (year_be, *academic_year_bounds(year_be)))
        if not db.execute("SELECT 1 FROM academic_years WHERE is_current = 1").fetchone():
            db.execute("UPDATE academic_years SET is_current = (year_be = ?)", (year_be,))

        # 5) Ensure at least one administrator exists
        if not db.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone():
            password = secrets.token_urlsafe(9)
            username = "admin" if not db.execute("SELECT 1 FROM users WHERE username='admin'").fetchone() else "sysadmin"
            db.execute("INSERT INTO users (username, password_hash, full_name, position, role) VALUES (?, ?, ?, ?, 'admin')",
                       (username, generate_password_hash(password), "ผู้ดูแลระบบ", "ผู้ดูแลระบบ"))
            note = Path(current_app.instance_path) / "initial_admin_password.txt"
            note.write_text(f"username: {username}\npassword: {password}\n"
                            "กรุณาเข้าสู่ระบบแล้วเปลี่ยนรหัสผ่าน จากนั้นลบไฟล์นี้\n", encoding="utf-8")
            print(f"[migrate] Created administrator account '{username}'. Password saved to {note}")

        # 6) Sub-task weights become percentages of the parent task (sum per task = 100)
        if not db.execute("SELECT 1 FROM settings WHERE key = 'subtask_weight_unit'").fetchone():
            convert_weights_to_percent(db)
            db.execute("INSERT INTO settings (key, value) VALUES ('subtask_weight_unit', 'percent')")
        db.commit()
    finally:
        db.close()
