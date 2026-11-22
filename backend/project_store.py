from .project_models import ACTIVE, Project
from .storage import Store


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
        for project in self.list_all_active():
            project.status = "interrupted"
            project.stage = "Processing interrupted"
            project.error = "Backend stopped. Resume completed work; another AI attempt requires explicit approval."
            self.save(project)

    def get(self, identifier: str) -> Project | None:
        with self.store.connection() as db:
            row = db.execute("SELECT payload FROM projects WHERE id=?", (identifier,)).fetchone()
            return Project.model_validate_json(row[0]) if row else None

    def list(self, limit=50, offset=0) -> list[Project]:
        with self.store.connection() as db:
            return [
                Project.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM projects ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
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

    def save(self, project: Project):
        payload = project.model_dump_json(exclude={"items": {"__all__": {"export"}}})
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "recording_id=excluded.recording_id,status=excluded.status,payload=excluded.payload",
                (project.id, project.recording_id, project.created_at, project.status, payload),
            )
