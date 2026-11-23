import json

from .highlight_models import Candidate, HighlightError, HighlightJob, ReviewRequest
from .storage import Store


class HighlightStore:
    def __init__(self, store: Store):
        self.store = store

    def initialize(self) -> None:
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS highlight_jobs (
                    id TEXT PRIMARY KEY,
                    recording_id TEXT NOT NULL REFERENCES recordings(id),
                    cache_key TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS highlights_recording_created
                    ON highlight_jobs(recording_id, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS highlights_cache ON highlight_jobs(cache_key, status);
                CREATE TABLE IF NOT EXISTS highlight_candidates (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES highlight_jobs(id),
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                    review TEXT NOT NULL DEFAULT 'unreviewed', revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS highlights_candidates_job ON highlight_candidates(job_id);
                CREATE INDEX IF NOT EXISTS highlights_candidates_fingerprint ON highlight_candidates(fingerprint);
                CREATE TABLE IF NOT EXISTS highlight_pools (
                    cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS highlight_discovery (
                    recording_id TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
                    cache_key TEXT NOT NULL, payload TEXT NOT NULL
                );
            """)

    def recover(self) -> None:
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT id,payload FROM highlight_jobs WHERE status IN ('queued','analysing','cancelling')"
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                payload.update(
                    status="failed",
                    error="Highlight analysis was interrupted. Retry explicitly; prior results and selections are preserved.",
                )
                db.execute(
                    "UPDATE highlight_jobs SET status='failed',payload=? WHERE id=?",
                    (json.dumps(payload), row["id"]),
                )

    @staticmethod
    def _read(db, row) -> HighlightJob | None:
        if row is None:
            return None
        job = HighlightJob.model_validate_json(row["payload"])
        transcript = db.execute(
            "SELECT id,revision,status FROM transcriptions WHERE recording_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (job.recording_id,),
        ).fetchone()
        job.stale = (
            transcript is None
            or transcript["id"] != job.transcription_id
            or transcript["revision"] != job.transcript_revision
            or transcript["status"] != "succeeded"
        )
        for item in db.execute(
            "SELECT payload,review,revision FROM highlight_candidates WHERE job_id=?", (job.id,)
        ):
            candidate = Candidate.model_validate_json(item["payload"])
            candidate.review, candidate.revision = item["review"], item["revision"]
            job.candidates.append(candidate)
        job.candidates.sort(key=lambda c: (-c.score, c.start, c.id))
        return job

    def job(self, identifier: str) -> HighlightJob | None:
        with self.store.connection() as db:
            db.execute("BEGIN")
            return self._read(
                db, db.execute("SELECT * FROM highlight_jobs WHERE id=?", (identifier,)).fetchone()
            )

    def latest(self, recording_id: str, *, succeeded: bool = False) -> HighlightJob | None:
        with self.store.connection() as db:
            db.execute("BEGIN")
            return self._read(
                db,
                db.execute(
                    "SELECT * FROM highlight_jobs WHERE recording_id=? AND (?=0 OR status='succeeded') ORDER BY created_at DESC,id DESC LIMIT 1",
                    (recording_id, succeeded),
                ).fetchone(),
            )

    def cached(self, key: str) -> HighlightJob | None:
        with self.store.connection() as db:
            db.execute("BEGIN")
            return self._read(
                db,
                db.execute(
                    "SELECT * FROM highlight_jobs WHERE cache_key=? AND status='succeeded' ORDER BY created_at DESC,id DESC LIMIT 1",
                    (key,),
                ).fetchone(),
            )

    def create(self, job: HighlightJob, key: str) -> None:
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO highlight_jobs VALUES (?,?,?,?,?,?)",
                (job.id, job.recording_id, key, job.status, job.model_dump_json(), job.created_at),
            )

    def discovery(
        self, recording_id: str, key: str
    ) -> tuple[list[Candidate], dict[str, int], list[dict], str | None, str | None] | None:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM highlight_discovery WHERE recording_id=? AND cache_key=?",
                (recording_id, key),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        return (
            [Candidate.model_validate(candidate) for candidate in payload["leads"]],
            payload["rejection_reasons"],
            payload.get("signals", []),
            payload.get("media_status"),
            payload.get("media_warning"),
        )

    def save_discovery(
        self,
        job: HighlightJob,
        key: str,
        leads: list[Candidate],
        rejection_reasons: dict[str, int],
        signals: list[dict],
    ) -> None:
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO highlight_discovery VALUES (?,?,?) ON CONFLICT(recording_id) "
                "DO UPDATE SET cache_key=excluded.cache_key,payload=excluded.payload",
                (
                    job.recording_id,
                    key,
                    json.dumps(
                        {
                            "leads": [candidate.model_dump() for candidate in leads],
                            "rejection_reasons": rejection_reasons,
                            "signals": signals,
                            "media_status": job.media_status,
                            "media_warning": job.media_warning,
                        }
                    ),
                ),
            )

    def update(self, identifier: str, **changes) -> None:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM highlight_jobs WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise RuntimeError("Highlight job is missing.")
            payload = json.loads(row["payload"])
            payload.update(changes)
            db.execute(
                "UPDATE highlight_jobs SET status=?,payload=? WHERE id=?",
                (payload["status"], json.dumps(payload), identifier),
            )

    def publish(
        self,
        job: HighlightJob,
        candidates: list[Candidate],
        rejected: int,
        *,
        pool: list[Candidate] | None = None,
    ) -> None:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute(
                "SELECT id,revision,status FROM transcriptions WHERE recording_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (job.recording_id,),
            ).fetchone()
            if (
                latest is None
                or latest["id"] != job.transcription_id
                or latest["revision"] != job.transcript_revision
                or latest["status"] != "succeeded"
            ):
                raise HighlightError(
                    "The transcript changed during analysis. Retry using its saved revision.", 409
                )
            for candidate in candidates:
                # Carry explicit reviews forward only for the same recording and exact source moment.
                prior = db.execute(
                    "SELECT c.review FROM highlight_candidates c JOIN highlight_jobs j ON j.id=c.job_id WHERE j.recording_id=? AND c.fingerprint=? ORDER BY j.created_at DESC,j.id DESC LIMIT 1",
                    (job.recording_id, candidate.fingerprint),
                ).fetchone()
                if prior:
                    candidate.review = prior["review"]
                db.execute(
                    "INSERT INTO highlight_candidates VALUES (?,?,?,?,?,?)",
                    (
                        candidate.id,
                        job.id,
                        candidate.fingerprint,
                        candidate.model_dump_json(),
                        candidate.review,
                        0,
                    ),
                )
            payload = json.loads(
                db.execute("SELECT payload FROM highlight_jobs WHERE id=?", (job.id,)).fetchone()[0]
            )
            payload.update(
                status="succeeded",
                editorial_status="completed",
                completed_windows=job.total_windows,
                rejected_candidates=rejected,
            )
            db.execute(
                "UPDATE highlight_jobs SET status='succeeded',payload=? WHERE id=?",
                (json.dumps(payload), job.id),
            )
            if pool is not None and job.pool_key:
                db.execute(
                    "INSERT INTO highlight_pools VALUES (?,?) ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload",
                    (job.pool_key, json.dumps([candidate.model_dump() for candidate in pool])),
                )

    def pool(self, key: str) -> list[Candidate]:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM highlight_pools WHERE cache_key=?", (key,)
            ).fetchone()
            if not row:
                return []
            result = [Candidate.model_validate(item) for item in json.loads(row[0])]
            # A review may have changed since discovery. Never revive an explicitly rejected moment.
            source = (
                db.execute(
                    "SELECT recording_id FROM highlight_jobs WHERE id=?", (result[0].job_id,)
                ).fetchone()
                if result
                else None
            )
            reviews = {}
            for item in db.execute(
                "SELECT c.fingerprint,c.review FROM highlight_candidates c JOIN highlight_jobs j ON j.id=c.job_id "
                "WHERE j.recording_id=? ORDER BY j.created_at DESC,j.id DESC",
                (source[0] if source else "",),
            ):
                reviews.setdefault(item[0], item[1])
            rejected = {
                fingerprint for fingerprint, review in reviews.items() if review == "rejected"
            }
            return [candidate for candidate in result if candidate.fingerprint not in rejected]

    def review(self, identifier: str, request: ReviewRequest) -> Candidate:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM highlight_candidates WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise HighlightError("Highlight candidate not found.", 404)
            if row["revision"] != request.revision:
                raise HighlightError(
                    "This review changed in another window. Reload before saving.", 409
                )
            result = Candidate.model_validate_json(row["payload"])
            if request.review == "accepted":
                job = HighlightJob.model_validate_json(
                    db.execute(
                        "SELECT payload FROM highlight_jobs WHERE id=?", (result.job_id,)
                    ).fetchone()[0]
                )
                transcript = db.execute(
                    "SELECT id,revision,status FROM transcriptions WHERE recording_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                    (job.recording_id,),
                ).fetchone()
                if (
                    transcript is None
                    or transcript["id"] != job.transcription_id
                    or transcript["revision"] != job.transcript_revision
                    or transcript["status"] != "succeeded"
                ):
                    raise HighlightError(
                        "The transcript changed. Generate current suggestions before accepting this moment.",
                        409,
                    )
            result.review, result.revision = request.review, request.revision + 1
            db.execute(
                "UPDATE highlight_candidates SET review=?,revision=? WHERE id=?",
                (result.review, result.revision, identifier),
            )
            return result
