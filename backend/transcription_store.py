import json
from collections.abc import Iterator
from pathlib import Path

from .storage import Store, now
from .transcription_models import (
    MAX_RESULT_BYTES,
    MAX_WORDS,
    EditableWord,
    RevisionResponse,
    TranscriptionError,
    TranscriptionJob,
    TranscriptWord,
    WordsPage,
    WordsPatch,
)


class TranscriptStore:
    def __init__(self, store: Store):
        self.store = store

    def initialize(self) -> None:
        # Called only after the application's exclusive data-directory lock is held.
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id TEXT PRIMARY KEY,
                    recording_id TEXT NOT NULL REFERENCES recordings(id),
                    status TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 1),
                    model TEXT NOT NULL, language TEXT NOT NULL,
                    error TEXT, warning TEXT, word_count INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 0,
                    source_hash TEXT, cache_key TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS transcriptions_recording_created
                    ON transcriptions(recording_id, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS transcriptions_cache
                    ON transcriptions(cache_key, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS transcript_words (
                    transcription_id TEXT NOT NULL REFERENCES transcriptions(id),
                    id INTEGER NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                    text TEXT NOT NULL, correction TEXT, probability REAL NOT NULL,
                    PRIMARY KEY(transcription_id, id), CHECK(end > start), CHECK(start >= 0)
                );
                CREATE INDEX IF NOT EXISTS transcript_words_interval
                    ON transcript_words(transcription_id, start);
                CREATE TABLE IF NOT EXISTS transcript_word_timings (
                    transcription_id TEXT NOT NULL, id INTEGER NOT NULL,
                    start REAL NOT NULL CHECK(start >= 0), end REAL NOT NULL CHECK(end > start),
                    PRIMARY KEY(transcription_id, id),
                    FOREIGN KEY(transcription_id, id) REFERENCES transcript_words(transcription_id, id)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(transcriptions)")}
            if "device" not in columns:
                db.execute("ALTER TABLE transcriptions ADD COLUMN device TEXT")

    def recover(self) -> None:
        with self.store.connection() as db:
            db.execute(
                "UPDATE transcriptions SET status='failed', error=?, updated_at=? "
                "WHERE status IN ('queued','preparing','transcribing','cancelling')",
                ("Transcription was interrupted. Retry to process the recording again.", now()),
            )

    def job(self, transcription_id: str) -> TranscriptionJob | None:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM transcriptions WHERE id=?", (transcription_id,)
            ).fetchone()
            return TranscriptionJob.model_validate(dict(row)) if row else None

    def latest(self, recording_id: str) -> TranscriptionJob | None:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM transcriptions WHERE recording_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (recording_id,),
            ).fetchone()
            return TranscriptionJob.model_validate(dict(row)) if row else None

    def create(self, transcription_id: str, recording_id: str) -> TranscriptionJob:
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO transcriptions "
                "(id,recording_id,status,model,language,created_at,updated_at) "
                "VALUES (?,?,'queued','small.en','en',?,?)",
                (transcription_id, recording_id, now(), now()),
            )
        job = self.job(transcription_id)
        if job is None:
            raise RuntimeError("The transcription job was not persisted.")
        return job

    def update(
        self,
        transcription_id: str,
        status: str,
        progress: float | None = None,
        error: str | None = None,
    ) -> None:
        with self.store.connection() as db:
            db.execute(
                "UPDATE transcriptions SET status=?, progress=COALESCE(?,progress), "
                "error=?, updated_at=? WHERE id=?",
                (status, progress, error, now(), transcription_id),
            )

    def reuse(self, transcription_id: str, source_hash: str, cache_key: str) -> bool:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            target = db.execute(
                "SELECT recording_id FROM transcriptions WHERE id=?", (transcription_id,)
            ).fetchone()
            if target is None:
                raise TranscriptionError("Transcription not found.", 404)
            cached = db.execute(
                "SELECT id,recording_id,word_count,revision,warning,device FROM transcriptions "
                "WHERE cache_key=? AND status='succeeded' "
                "ORDER BY (recording_id=?) DESC,updated_at DESC LIMIT 1",
                (cache_key, target["recording_id"]),
            ).fetchone()
            if cached is None:
                return False
            db.execute(
                "INSERT INTO transcript_words "
                "SELECT ?,id,start,end,text,CASE WHEN ? THEN correction ELSE NULL END,probability FROM transcript_words "
                "WHERE transcription_id=?",
                (transcription_id, cached["recording_id"] == target["recording_id"], cached["id"]),
            )
            db.execute(
                "UPDATE transcriptions SET status='succeeded',progress=1,word_count=?,"
                "revision=?,source_hash=?,cache_key=?,warning=?,device=?,updated_at=? WHERE id=?",
                (
                    cached["word_count"],
                    cached["revision"] if cached["recording_id"] == target["recording_id"] else 1,
                    source_hash,
                    cache_key,
                    cached["warning"],
                    cached["device"],
                    now(),
                    transcription_id,
                ),
            )
            if cached["recording_id"] == target["recording_id"]:
                db.execute(
                    "INSERT INTO transcript_word_timings SELECT ?,id,start,end "
                    "FROM transcript_word_timings WHERE transcription_id=?",
                    (transcription_id, cached["id"]),
                )
            return True

    def publish(
        self,
        transcription_id: str,
        source_hash: str,
        cache_key: str,
        result: Path,
        duration: float,
        expected_count: int,
        warning: str | None = None,
        device: str | None = None,
    ) -> None:
        if result.stat().st_size > MAX_RESULT_BYTES:
            raise TranscriptionError("Transcription exceeded its result size limit.")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            count = 0
            previous_start = -1.0
            for word in self.read_result(result):
                if word.id != count or word.start < previous_start or word.end > duration + 0.05:
                    raise TranscriptionError("Transcription returned invalid word timing.")
                if not word.text or word.edited:
                    raise TranscriptionError("Transcription returned an invalid word.")
                db.execute(
                    "INSERT INTO transcript_words VALUES (?,?,?,?,?,NULL,?)",
                    (transcription_id, word.id, word.start, word.end, word.text, word.probability),
                )
                previous_start = word.start
                count += 1
            if count != expected_count:
                raise TranscriptionError(
                    "Transcription output is incomplete. Retry to process it again."
                )
            db.execute(
                "UPDATE transcriptions SET status='succeeded',progress=1,word_count=?,"
                "revision=1,source_hash=?,cache_key=?,warning=?,device=?,updated_at=? WHERE id=?",
                (count, source_hash, cache_key, warning, device, now(), transcription_id),
            )

    @staticmethod
    def read_result(path: Path) -> Iterator[TranscriptWord]:
        with path.open("r", encoding="utf-8") as output:
            for index, line in enumerate(output):
                if index >= MAX_WORDS or len(line) > 4096:
                    raise TranscriptionError("Transcription exceeded its word limit.")
                try:
                    yield TranscriptWord.model_validate(json.loads(line))
                except ValueError as exc:
                    raise TranscriptionError("Transcription returned an invalid result.") from exc

    def words(
        self,
        transcription_id: str,
        start: float,
        end: float,
        revision: int | None = None,
        recording_id: str | None = None,
    ) -> WordsPage:
        with self.store.connection() as db:
            # Both revision and word rows must come from the same snapshot.
            db.execute("BEGIN")
            job = db.execute(
                "SELECT * FROM transcriptions WHERE id=?", (transcription_id,)
            ).fetchone()
            if job is None or (recording_id is not None and job["recording_id"] != recording_id):
                raise TranscriptionError("Transcription not found for this recording.", 404)
            if job["status"] != "succeeded":
                raise TranscriptionError(
                    "Transcription must finish before captions can be used.", 409
                )
            if revision is not None and job["revision"] != revision:
                raise TranscriptionError("Captions changed. Reload them before exporting.", 409)
            rows = db.execute(
                "SELECT w.id,COALESCE(t.start,w.start) AS start,COALESCE(t.end,w.end) AS end,"
                "w.start AS original_start,w.end AS original_end,"
                "COALESCE(w.correction,w.text) AS text,w.probability,"
                "w.correction IS NOT NULL AS edited,t.id IS NOT NULL AS timing_edited "
                "FROM transcript_words w LEFT JOIN transcript_word_timings t "
                "ON t.transcription_id=w.transcription_id AND t.id=w.id "
                "WHERE w.transcription_id=? AND COALESCE(t.start,w.start) < ? "
                "AND COALESCE(t.end,w.end) > ? ORDER BY w.id",
                (transcription_id, end, start),
            )
            return WordsPage(
                id=transcription_id,
                revision=job["revision"],
                words=[EditableWord.model_validate(dict(row)) for row in rows],
            )

    def patch(self, transcription_id: str, request: WordsPatch) -> RevisionResponse:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute(
                "SELECT t.status,t.revision,r.duration FROM transcriptions t "
                "JOIN recordings r ON r.id=t.recording_id WHERE t.id=?",
                (transcription_id,),
            ).fetchone()
            if job is None:
                raise TranscriptionError("Transcription not found.", 404)
            if job["status"] != "succeeded":
                raise TranscriptionError("Transcription must finish before editing captions.", 409)
            if job["revision"] != request.revision:
                raise TranscriptionError(
                    "Captions changed in another session. Reload before saving.", 409
                )
            changed = False
            for update in request.updates:
                row = db.execute(
                    "SELECT text,correction,start,end FROM transcript_words WHERE transcription_id=? AND id=?",
                    (transcription_id, update.id),
                ).fetchone()
                if row is None:
                    raise TranscriptionError("One or more caption words no longer exist.", 409)
                if update.start is not None:
                    if update.end > job["duration"]:
                        raise TranscriptionError("Word timing must remain inside the recording.")
                    current = db.execute(
                        "SELECT start,end FROM transcript_word_timings WHERE transcription_id=? AND id=?",
                        (transcription_id, update.id),
                    ).fetchone()
                    current_start = current["start"] if current else row["start"]
                    current_end = current["end"] if current else row["end"]
                    if (update.start, update.end) != (current_start, current_end):
                        changed = True
                        if (update.start, update.end) == (row["start"], row["end"]):
                            db.execute(
                                "DELETE FROM transcript_word_timings WHERE transcription_id=? AND id=?",
                                (transcription_id, update.id),
                            )
                        else:
                            db.execute(
                                "INSERT INTO transcript_word_timings VALUES (?,?,?,?) "
                                "ON CONFLICT(transcription_id,id) DO UPDATE SET start=excluded.start,end=excluded.end",
                                (transcription_id, update.id, update.start, update.end),
                            )
                correction = None if update.text == row["text"] else update.text
                if correction != row["correction"]:
                    changed = True
                    db.execute(
                        "UPDATE transcript_words SET correction=? WHERE transcription_id=? AND id=?",
                        (correction, transcription_id, update.id),
                    )
            # Validate after the complete batch so adjacent words can move together.
            # Genuine ASR overlaps are supported; starts must retain transcript order.
            for update in request.updates:
                if update.start is None:
                    continue
                neighbors = db.execute(
                    "SELECT COALESCE(t.start,w.start) AS start FROM transcript_words w "
                    "LEFT JOIN transcript_word_timings t ON t.transcription_id=w.transcription_id AND t.id=w.id "
                    "WHERE w.transcription_id=? AND w.id BETWEEN ? AND ? ORDER BY w.id",
                    (transcription_id, max(0, update.id - 1), update.id + 1),
                ).fetchall()
                if any(a["start"] > b["start"] for a, b in zip(neighbors, neighbors[1:])):
                    raise TranscriptionError("Word starts must remain in transcript order.")
            revision = request.revision + int(changed)
            db.execute(
                "UPDATE transcriptions SET revision=?,updated_at=? WHERE id=?",
                (revision, now(), transcription_id),
            )
            return RevisionResponse(id=transcription_id, revision=revision)
