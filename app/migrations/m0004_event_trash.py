"""سلة المحذوفات: الحذف الناعم للفعاليات (deleted_at/deleted_by). الفعاليات الحالية تبقى كما هي (deleted_at = NULL)."""
DESCRIPTION = "events: deleted_at, deleted_by + index"


def up(c):
    have = c.columns("events")        # يعمل على SQLite وPostgreSQL
    for col in ("deleted_at", "deleted_by"):
        if col not in have:
            c.ddl(f"ALTER TABLE events ADD COLUMN {col} INTEGER")
    c.ddl("CREATE INDEX IF NOT EXISTS ix_events_deleted ON events(deleted_at)")
