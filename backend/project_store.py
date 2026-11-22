import sqlite3
from uuid import uuid4

from .highlight_models import SourceRange
from .highlight_selection import merge_ranges
from .project_models import ACTIVE, ItemEdit, Project
from .storage import Store, now


class ProjectStore:
    def __init__(self, store: Store):
        self.store = store

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    recording_id TEXT REFERENCES recordings(id),
                    created_at TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS projects_created ON projects(created_at DESC,id DESC);
                CREATE TABLE IF NOT EXISTS project_presets (id TEXT PRIMARY KEY, name TEXT NOT NULL, workflow TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS project_item_versions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL, created_at TEXT NOT NULL, export_id TEXT, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS project_item_versions_item ON project_item_versions(project_id,item_id,created_at DESC);
            """)
            columns = db.execute("PRAGMA table_info(projects)").fetchall()
            if next(column["notnull"] for column in columns if column["name"] == "recording_id"):
                # Preserve existing projects while allowing a preview before media exists.
                db.execute("BEGIN")
                db.execute(
                    "CREATE TABLE projects_migrating (id TEXT PRIMARY KEY, recording_id TEXT REFERENCES recordings(id), created_at TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)"
                )
                db.execute("INSERT INTO projects_migrating SELECT * FROM projects")
                db.execute("DROP TABLE projects")
                db.execute("ALTER TABLE projects_migrating RENAME TO projects")
                db.execute("CREATE INDEX projects_created ON projects(created_at DESC,id DESC)")
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_selections'"
            ).fetchone():
                if not db.in_transaction:
                    db.execute("BEGIN IMMEDIATE")
                db.execute("""
                    CREATE TABLE project_selections (
                        recording_id TEXT NOT NULL REFERENCES recordings(id),
                        start REAL NOT NULL CHECK(start>=0), end REAL NOT NULL CHECK(end>start),
                        PRIMARY KEY(recording_id,start,end)
                    )
                """)
                for row in db.execute("SELECT payload FROM projects"):
                    self._remember(Project.model_validate_json(row[0]), db)
        for project in self.list_all_active():
            project.status = "interrupted"
            project.stage = "Processing interrupted"
            project.error = "Backend stopped. Resume completed work; another AI attempt requires explicit approval."
            self.save(project)

    def get(self, identifier: str) -> Project | None:
        with self.store.connection() as db:
            row = db.execute("SELECT payload FROM projects WHERE id=?", (identifier,)).fetchone()
            return Project.model_validate_json(row[0]) if row else None

    def list(self, limit=50, offset=0, *, search="", status="all", sort="newest") -> list[Project]:
        order = {
            "newest": "created_at DESC,id DESC",
            "oldest": "created_at,id",
            "title": "lower(json_extract(payload,'$.title')),id",
        }[sort]
        statuses = sorted(ACTIVE) if status == "processing" else [status]
        predicate = "" if status == "all" else f" AND status IN ({','.join('?' for _ in statuses)})"
        with self.store.connection() as db:
            return [
                Project.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM projects WHERE instr(lower(json_extract(payload,'$.title')),lower(?))>0"
                    + predicate
                    + " ORDER BY "
                    + order
                    + " LIMIT ? OFFSET ?",
                    (search, *([] if status == "all" else statuses), limit, offset),
                )
            ]

    def remember_version(self, project, item_id, db):
        item = next(i for i in project.items if i.id == item_id)
        edit = ItemEdit.model_validate(
            {key: getattr(item, key) for key in ItemEdit.model_fields if key != "auto_presentation"}
        )
        db.execute(
            "INSERT INTO project_item_versions VALUES(?,?,?,?,?,?)",
            (str(uuid4()), project.id, item.id, now(), item.export_id, edit.model_dump_json()),
        )
        db.execute(
            "DELETE FROM project_item_versions WHERE project_id=? AND item_id=? AND id NOT IN (SELECT id FROM project_item_versions WHERE project_id=? AND item_id=? ORDER BY created_at DESC,id DESC LIMIT 50)",
            (project.id, item.id, project.id, item.id),
        )

    def versions(self, project_id, item_id):
        with self.store.connection() as db:
            return [
                {
                    **dict(row),
                    "settings": ItemEdit.model_validate_json(row["payload"]).model_dump(
                        mode="json"
                    ),
                }
                for row in db.execute(
                    "SELECT * FROM project_item_versions WHERE project_id=? AND item_id=? ORDER BY created_at DESC,id DESC LIMIT 50",
                    (project_id, item_id),
                )
            ]

    def list_all_active(self) -> list[Project]:
        with self.store.connection() as db:
            return [
                Project.model_validate_json(row[0])
                for row in db.execute(
                    f"SELECT payload FROM projects WHERE status IN ({','.join('?' for _ in ACTIVE)})",
                    tuple(sorted(ACTIVE)),
                )
            ]

    def save(self, project: Project, *, db: sqlite3.Connection | None = None) -> None:
        if db is None:
            with self.store.connection() as connection:
                return self.save(project, db=connection)
        payload = project.model_dump_json(exclude={"items": {"__all__": {"export"}}})
        db.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "recording_id=excluded.recording_id,status=excluded.status,payload=excluded.payload",
            (project.id, project.recording_id, project.created_at, project.status, payload),
        )
        self._remember(project, db)

    @staticmethod
    def _remember(project: Project, db: sqlite3.Connection):
        # History deliberately has no project/clip foreign key. Deletion and
        # rejection must not make a previously suggested moment new again.
        if project.recording_id and not project.recording_ids and project.selection_complete:
            db.executemany(
                "INSERT OR IGNORE INTO project_selections(recording_id,start,end) VALUES(?,?,?)",
                [(project.recording_id, item.start, item.end) for item in project.items],
            )

    def used_ranges(self, recording_id: str) -> list[SourceRange]:
        with self.store.connection() as db:
            return merge_ranges(
                [
                    SourceRange(start=row[0], end=row[1])
                    for row in db.execute(
                        "SELECT start,end FROM project_selections WHERE recording_id=? ORDER BY start,end",
                        (recording_id,),
                    )
                ]
            )
