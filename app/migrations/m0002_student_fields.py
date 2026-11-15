"""إضافة الحالة الأكاديمية والجنس والجنسية لجدول المشاركين (السجلات القديمة تبقى «غير محدد»)."""
DESCRIPTION = "participants: academic_status, gender, nationality"


def up(c):
    have = c.columns("participants")      # يعمل على SQLite وPostgreSQL
    for col in ("academic_status", "gender", "nationality"):
        if col not in have:
            c.execute(f"ALTER TABLE participants ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    c.execute("CREATE INDEX IF NOT EXISTS ix_part_demo ON participants(gender, nationality, academic_status)")
