"""Create a fresh database with demo users, tasks, sub-tasks and reports.

Usage:  python seed.py      (WARNING: drops all existing data)
"""
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from taskapp import create_app
from taskapp.db import academic_year_bounds, academic_year_of, get_db, init_db

DEMO_PASSWORD = "password123"


def d(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


def seed():
    init_db()
    db = get_db()
    db.execute("INSERT INTO settings (key, value) VALUES ('org_name', 'สำนักเทคโนโลยีสารสนเทศ')")

    # Academic years: previous, current (flagged) and next
    current_be = academic_year_of(date.today())
    year_ids = {}
    for be in (current_be - 1, current_be, current_be + 1):
        start, end = academic_year_bounds(be)
        cur = db.execute("INSERT INTO academic_years (year_be, start_date, end_date, is_current) VALUES (?, ?, ?, ?)",
                         (be, start, end, int(be == current_be)))
        year_ids[be] = cur.lastrowid

    # Departments (หน่วยงาน) and their divisions (แผนก)
    org = {
        "สำนักเทคโนโลยีสารสนเทศ": ["ฝ่ายพัฒนาระบบ", "ฝ่ายโครงสร้างพื้นฐาน"],
        "สำนักงานอธิการบดี": ["งานนโยบายและแผน", "งานบริหารทั่วไป", "งานการเงินและบัญชี"],
    }
    dept_ids, div_ids = {}, {}
    for dept_name, divs in org.items():
        dept_ids[dept_name] = db.execute("INSERT INTO departments (name) VALUES (?)", (dept_name,)).lastrowid
        for div_name in divs:
            div_ids[div_name] = db.execute("INSERT INTO divisions (department_id, name) VALUES (?, ?)",
                                           (dept_ids[dept_name], div_name)).lastrowid

    IT, PRES = "สำนักเทคโนโลยีสารสนเทศ", "สำนักงานอธิการบดี"
    users = [
        ("admin", "ผู้ดูแลระบบ", "ผู้ดูแลระบบ", "admin", IT, "ฝ่ายโครงสร้างพื้นฐาน"),
        ("manager", "สมชาย ใจดี", "หัวหน้าหน่วยงาน", "manager", IT, None),
        ("prasit", "ประสิทธิ์ ก้าวหน้า", "หัวหน้าฝ่ายพัฒนาระบบ", "division_head", IT, "ฝ่ายพัฒนาระบบ"),
        ("malee", "มาลี ศรีสุข", "หัวหน้างานนโยบายและแผน", "division_head", PRES, "งานนโยบายและแผน"),
        ("somsri", "สมศรี รักงาน", "นักวิชาการคอมพิวเตอร์", "staff", IT, "ฝ่ายพัฒนาระบบ"),
        ("wichai", "วิชัย มั่นคง", "นักวิเคราะห์นโยบายและแผน", "staff", PRES, "งานนโยบายและแผน"),
        ("napa", "นภา สุขใจ", "เจ้าหน้าที่บริหารงานทั่วไป", "staff", PRES, "งานบริหารทั่วไป"),
        ("anan", "อนันต์ พากเพียร", "นักวิชาการเงินและบัญชี", "staff", PRES, "งานการเงินและบัญชี"),
    ]
    ids = {}
    for username, name, position, role, dept, div in users:
        cur = db.execute(
            """INSERT INTO users (username, password_hash, full_name, position, role, department_id, division_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (username, generate_password_hash(DEMO_PASSWORD), name, position, role, dept_ids[dept], div_ids.get(div)),
        )
        ids[username] = cur.lastrowid
    mgr = ids["manager"]

    def task(title, desc, start, due, priority, status, assignees, manual=0):
        be = academic_year_of(date.today() + timedelta(days=start))
        if be not in year_ids:
            s, e = academic_year_bounds(be)
            year_ids[be] = db.execute("INSERT INTO academic_years (year_be, start_date, end_date) VALUES (?, ?, ?)",
                                      (be, s, e)).lastrowid
        cur = db.execute(
            """INSERT INTO tasks (title, description, start_date, due_date, priority, status, manual_progress,
                                  academic_year_id, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, desc, d(start), d(due), priority, status, manual, year_ids[be], mgr),
        )
        for a in assignees:
            db.execute("INSERT INTO task_assignees (task_id, user_id) VALUES (?, ?)", (cur.lastrowid, ids[a]))
        return cur.lastrowid

    def sub(task_id, title, who, due, weight, progress, status):
        db.execute(
            "INSERT INTO subtasks (task_id, title, assignee_id, due_date, weight, progress, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, title, ids[who], d(due), weight, progress, status),
        )

    def report(task_id, who, day, result, problems="", obstacles="", help_needed="", status=None):
        issue = status or ("open" if problems or obstacles else "none")
        cur = db.execute(
            """INSERT INTO reports (task_id, user_id, report_date, result, problems, obstacles, help_needed, issue_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, ids[who], d(day), result, problems, obstacles, help_needed, issue),
        )
        return cur.lastrowid

    t1 = task("พัฒนาระบบจองห้องประชุมออนไลน์", "พัฒนาเว็บแอปสำหรับจองห้องประชุมของหน่วยงาน พร้อมเชื่อมต่อปฏิทิน",
              -20, 15, "high", "in_progress", ["somsri", "wichai"])
    sub(t1, "เก็บความต้องการผู้ใช้", "wichai", -12, 15, 100, "done")
    sub(t1, "ออกแบบฐานข้อมูลและหน้าจอ", "somsri", -3, 25, 100, "done")
    sub(t1, "พัฒนาระบบ (Backend + Frontend)", "somsri", 8, 45, 40, "in_progress")
    sub(t1, "ทดสอบและฝึกอบรมผู้ใช้", "wichai", 14, 15, 0, "not_started")
    r = report(t1, "somsri", -5, "ออกแบบฐานข้อมูลเสร็จแล้ว เริ่มพัฒนาส่วนจัดการห้อง",
               obstacles="เซิร์ฟเวอร์ทดสอบมีพื้นที่ไม่เพียงพอ", help_needed="ขอเพิ่มพื้นที่ดิสก์ 50 GB", status="resolved")
    db.execute("INSERT INTO report_comments (report_id, user_id, body) VALUES (?, ?, ?)",
               (r, mgr, "ประสานฝ่ายไอทีเพิ่มพื้นที่ให้แล้วครับ"))
    report(t1, "somsri", -1, "พัฒนาหน้าจองห้องได้ประมาณ 40%",
           problems="API ปฏิทินของหน่วยงานยังไม่เปิดสิทธิ์ให้เรียกใช้", help_needed="ขอให้หัวหน้าช่วยประสานขอสิทธิ์ API")

    t2 = task("จัดทำแผนปฏิบัติการประจำปี 2570", "รวบรวมโครงการและงบประมาณจากทุกฝ่ายเพื่อจัดทำแผนปฏิบัติการ",
              -30, -2, "urgent", "in_progress", ["wichai", "anan"])
    sub(t2, "รวบรวมข้อมูลโครงการจากทุกฝ่าย", "wichai", -15, 20, 100, "done")
    sub(t2, "จัดทำประมาณการงบประมาณ", "anan", -5, 40, 60, "in_progress")
    sub(t2, "จัดทำร่างแผนและเสนอผู้บริหาร", "wichai", -2, 40, 20, "in_progress")
    report(t2, "anan", -3, "ประมาณการงบประมาณได้ 60%",
           problems="ข้อมูลงบประมาณจากบางฝ่ายยังไม่ครบ", obstacles="ระบบบัญชีปิดปรับปรุง 2 วัน")

    t3 = task("ปรับปรุงเว็บไซต์หน่วยงาน", "ปรับหน้าข่าวประชาสัมพันธ์และข้อมูลติดต่อให้เป็นปัจจุบัน",
              -7, 2, "medium", "in_progress", ["napa"], manual=70)
    report(t3, "napa", -2, "อัปเดตข้อมูลติดต่อและหน้าข่าวแล้ว เหลือหน้ารวมเอกสารดาวน์โหลด")

    t4 = task("จัดซื้อครุภัณฑ์คอมพิวเตอร์", "จัดซื้อคอมพิวเตอร์ทดแทนเครื่องเก่า 10 เครื่อง",
              -40, -10, "high", "done", ["anan", "napa"])
    sub(t4, "สำรวจความต้องการ", "napa", -35, 20, 100, "done")
    sub(t4, "จัดทำ TOR และดำเนินการจัดซื้อ", "anan", -15, 60, 100, "done")
    sub(t4, "ตรวจรับครุภัณฑ์", "anan", -10, 20, 100, "done")

    task("อบรมความปลอดภัยไซเบอร์ให้บุคลากร", "จัดอบรมหลักสูตรพื้นฐานด้านความปลอดภัยไซเบอร์",
         5, 30, "low", "not_started", ["somsri", "napa"])

    task("สำรวจความพึงพอใจผู้รับบริการ", "ออกแบบแบบสอบถามและสรุปผลความพึงพอใจไตรมาส 4",
         -10, 20, "medium", "on_hold", ["napa"], manual=30)

    # A finished task from the previous academic year
    t8 = task("ประเมินผลการดำเนินงานประจำปีการศึกษาที่ผ่านมา", "สรุปผลการดำเนินงานตามตัวชี้วัดของปีการศึกษาก่อน",
              -150, -110, "medium", "done", ["wichai", "somsri"])
    sub(t8, "รวบรวมผลตัวชี้วัด", "wichai", -130, 35, 100, "done")
    sub(t8, "จัดทำรายงานสรุปผล", "somsri", -110, 65, 100, "done")
    report(t8, "wichai", -112, "จัดทำรายงานสรุปผลเสร็จและเสนอผู้บริหารแล้ว")

    t7 = task("จัดทำรายงานการเงินประจำไตรมาส", "สรุปรายรับ-รายจ่ายประจำไตรมาสเสนอผู้บริหาร",
              -5, 1, "high", "in_progress", ["anan"])
    sub(t7, "กระทบยอดบัญชี", "anan", 0, 70, 80, "in_progress")
    sub(t7, "จัดทำรายงานสรุป", "anan", 1, 30, 0, "not_started")

    notifications = [
        (ids["somsri"], "assigned", "คุณได้รับมอบหมายงานใหม่: “อบรมความปลอดภัยไซเบอร์ให้บุคลากร”"),
        (ids["napa"], "assigned", "คุณได้รับมอบหมายงานใหม่: “อบรมความปลอดภัยไซเบอร์ให้บุคลากร”"),
        (mgr, "issue", "สมศรี รักงาน รายงานปัญหา/อุปสรรคในงาน “พัฒนาระบบจองห้องประชุมออนไลน์”"),
        (mgr, "issue", "อนันต์ พากเพียร รายงานปัญหา/อุปสรรคในงาน “จัดทำแผนปฏิบัติการประจำปี 2570”"),
    ]
    for uid, ntype, msg in notifications:
        db.execute("INSERT INTO notifications (user_id, type, message, link) VALUES (?, ?, ?, ?)",
                   (uid, ntype, msg, "/"))
    db.commit()
    print("Seeded demo data. Users: admin / manager / prasit / malee (division heads) / somsri / wichai / napa / anan  (password: %s)" % DEMO_PASSWORD)


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed()
