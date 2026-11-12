-- ระบบมอบหมายและติดตามงาน (Task Management System) - SQLite schema
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS notifications;
DROP TABLE IF EXISTS report_comments;
DROP TABLE IF EXISTS report_attachments;
DROP TABLE IF EXISTS reports;
DROP TABLE IF EXISTS subtasks;
DROP TABLE IF EXISTS task_assignees;
DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS academic_years;
DROP TABLE IF EXISTS settings;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS role_permissions;
DROP TABLE IF EXISTS roles;
DROP TABLE IF EXISTS divisions;
DROP TABLE IF EXISTS departments;

-- บทบาท: ขอบเขตการมองเห็นงาน (scope) + สิทธิ์ (role_permissions) — ผู้ดูแลระบบปรับได้ที่เมนู “จัดการบทบาท”
CREATE TABLE roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,      -- admin / manager / division_head / staff / custom_N
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    scope       TEXT    NOT NULL DEFAULT 'own' CHECK (scope IN ('all', 'division', 'own')),
    is_system   INTEGER NOT NULL DEFAULT 0,  -- บทบาทพื้นฐาน ลบไม่ได้
    sort_order  INTEGER NOT NULL DEFAULT 100,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE role_permissions (
    role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission TEXT    NOT NULL,
    PRIMARY KEY (role_id, permission)
);

-- หน่วยงาน
CREATE TABLE departments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- แผนก (อยู่ภายใต้หน่วยงาน)
CREATE TABLE divisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    name          TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (department_id, name)
);

CREATE TABLE users (
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

-- ปีการศึกษา เช่น 2569 = 1 ส.ค. 2569 (2026-08-01) ถึง 31 ก.ค. 2570 (2027-07-31)
CREATE TABLE academic_years (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    year_be    INTEGER NOT NULL UNIQUE,   -- ปี พ.ศ.
    start_date TEXT    NOT NULL,
    end_date   TEXT    NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (start_date < end_date)
);

-- ค่าตั้งค่าระบบ (key/value) ที่ผู้ดูแลระบบปรับได้
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- ฐานข้อมูลใหม่ใช้น้ำหนักงานย่อยเป็นเปอร์เซ็นต์ตั้งแต่แรก (migrate_db จะไม่แปลงซ้ำ)
INSERT INTO settings (key, value) VALUES ('subtask_weight_unit', 'percent');

CREATE TABLE tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    description     TEXT,
    start_date      TEXT,                      -- YYYY-MM-DD
    due_date        TEXT,                      -- YYYY-MM-DD
    priority        TEXT    NOT NULL DEFAULT 'medium'
                    CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    status          TEXT    NOT NULL DEFAULT 'not_started'
                    CHECK (status IN ('not_started', 'in_progress', 'done', 'on_hold')),
    manual_progress REAL    NOT NULL DEFAULT 0 CHECK (manual_progress BETWEEN 0 AND 100),
    academic_year_id INTEGER REFERENCES academic_years(id),
    created_by      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Many-to-Many: Task <-> ผู้รับผิดชอบ
CREATE TABLE task_assignees (
    task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (task_id, user_id)
);

CREATE TABLE subtasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    due_date    TEXT,
    weight      REAL    NOT NULL DEFAULT 100 CHECK (weight > 0 AND weight <= 100),  -- % ของงานหลัก, รวมทุกงานย่อยไม่เกิน 100
    progress    REAL    NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    status      TEXT    NOT NULL DEFAULT 'not_started'
                CHECK (status IN ('not_started', 'in_progress', 'done', 'on_hold')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- รายงานผลการดำเนินงาน (หลายครั้งต่อ Task -> Timeline)
CREATE TABLE reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    report_date  TEXT    NOT NULL,
    result       TEXT,
    problems     TEXT,
    obstacles    TEXT,
    help_needed  TEXT,
    -- none = ไม่มีปัญหา, open = รอแก้ไข, resolved = แก้ไขแล้ว
    issue_status TEXT    NOT NULL DEFAULT 'none' CHECK (issue_status IN ('none', 'open', 'resolved')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE report_attachments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id     INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    original_name TEXT    NOT NULL,
    stored_name   TEXT    NOT NULL UNIQUE,
    size_bytes    INTEGER NOT NULL,
    uploaded_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE report_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id  INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    body       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type       TEXT    NOT NULL,   -- assigned / due_soon / overdue / issue / comment
    message    TEXT    NOT NULL,
    link       TEXT,
    is_read    INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT    UNIQUE,     -- กันแจ้งเตือนซ้ำ (เช่น ใกล้ครบกำหนด/เลยกำหนด)
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX idx_tasks_status      ON tasks(status);
CREATE INDEX idx_tasks_due         ON tasks(due_date);
CREATE INDEX idx_tasks_year        ON tasks(academic_year_id);
CREATE INDEX idx_assignees_user    ON task_assignees(user_id);
CREATE INDEX idx_subtasks_task     ON subtasks(task_id);
CREATE INDEX idx_subtasks_assignee ON subtasks(assignee_id);
CREATE INDEX idx_reports_task      ON reports(task_id);
CREATE INDEX idx_notif_user        ON notifications(user_id, is_read);
