"""Permission catalogue, access scopes and the built-in roles (editable by the admin in "จัดการบทบาท")."""

# key -> (label, help text). Order = display order in the role matrix.
PERMISSIONS = {
    "dashboard.overview": ("ดูแดชบอร์ดภาพรวมและภาระงานทีม",
                           "เห็นแดชบอร์ดหัวหน้า: สถิติงาน งานเลยกำหนด ปัญหาที่ยังไม่แก้ และภาระงานของสมาชิกในขอบเขต"),
    "task.create": ("สร้างงาน", "เพิ่มงานใหม่ได้ (ถ้าไม่มีสิทธิ์มอบหมาย งานจะมอบหมายให้ตนเองอัตโนมัติ)"),
    "task.assign": ("มอบหมายงานให้ผู้อื่น",
                    "เลือกผู้รับผิดชอบงาน/งานย่อยจากผู้ใช้ในขอบเขต และมอบหมายงานที่ตนเห็นให้ลูกน้องต่อได้"),
    "task.manage": ("แก้ไขงานของผู้อื่น",
                    "แก้ไข เปลี่ยนสถานะ และจัดการงานย่อยของงานที่ผู้อื่นสร้าง (ภายในขอบเขต)"),
    "task.delete": ("ลบงานของผู้อื่น", "ลบงานที่ผู้อื่นสร้าง (ภายในขอบเขต) — งานที่ตนสร้างเองลบได้เสมอ"),
    "report.review": ("ตอบกลับรายงานและปิดสถานะปัญหา", "แสดงความคิดเห็นในรายงานของผู้อื่น และระบุว่าปัญหาแก้ไขแล้ว"),
    "notify.supervisor": ("รับแจ้งเตือนในฐานะหัวหน้า",
                          "ได้รับแจ้งเมื่อผู้ใช้ในขอบเขตรายงานปัญหา/อุปสรรค ตอบกลับรายงาน หรือเพิ่มงานเอง"),
    "task.receive": ("รับมอบหมายงานได้", "ปรากฏในรายชื่อผู้รับผิดชอบ และในตารางภาระงาน"),
}

SCOPE_LABELS = {
    "all": "ทั้งหน่วยงาน",
    "division": "เฉพาะแผนกของตน",
    "own": "เฉพาะงานของตน",
}
SCOPE_HELP = {
    "all": "เห็นและทำงานกับทุกงานในระบบ",
    "division": "เห็นงานที่สมาชิกในแผนกเดียวกันเกี่ยวข้อง และมอบหมาย/จัดการได้เฉพาะสมาชิกในแผนก (ผู้ใช้ต้องระบุแผนก)",
    "own": "เห็นเฉพาะงานที่ได้รับมอบหมายหรือตนสร้าง",
}

ADMIN_ROLE = "admin"
# The admin role always keeps these (it is locked in the UI so nobody can lock themselves out).
ADMIN_PERMISSIONS = {"dashboard.overview", "task.create", "task.assign", "task.manage", "task.delete", "report.review"}

# code -> (name, description, scope, sort_order, permissions)
DEFAULT_ROLES = {
    "admin": ("ผู้ดูแลระบบ", "บริหารจัดการระบบทั้งหมด (ผู้ใช้ บทบาท หน่วยงาน ปีการศึกษา ตั้งค่า)", "all", 1,
              ADMIN_PERMISSIONS),
    "manager": ("หัวหน้าหน่วยงาน", "มอบหมายและติดตามงานทั้งหน่วยงาน", "all", 2,
                {"dashboard.overview", "task.create", "task.assign", "task.manage", "task.delete",
                 "report.review", "notify.supervisor"}),
    "division_head": ("หัวหน้าแผนก", "มอบหมายและติดตามงานของลูกน้องในแผนก", "division", 3,
                      {"dashboard.overview", "task.create", "task.assign", "task.manage", "task.delete",
                       "report.review", "notify.supervisor", "task.receive"}),
    "staff": ("พนักงาน", "ปฏิบัติงานที่ได้รับมอบหมาย และรายงานผล", "own", 4, {"task.create", "task.receive"}),
}


def seed_roles(db):
    """Insert the built-in roles (and their default permissions) that are missing. Idempotent."""
    for code, (name, desc, scope, order, perms) in DEFAULT_ROLES.items():
        if db.execute("SELECT 1 FROM roles WHERE code = ?", (code,)).fetchone():
            continue
        taken = db.execute("SELECT 1 FROM roles WHERE name = ?", (name,)).fetchone()
        cur = db.execute(
            "INSERT INTO roles (code, name, description, scope, is_system, sort_order) VALUES (?, ?, ?, ?, 1, ?)",
            (code, f"{name} ({code})" if taken else name, desc, scope, order))
        db.executemany("INSERT INTO role_permissions (role_id, permission) VALUES (?, ?)",
                       [(cur.lastrowid, p) for p in sorted(perms)])
