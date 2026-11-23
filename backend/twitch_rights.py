"""Rights and duplicate checks shared by Twitch processing and existing publishers."""

import json
import time
from contextlib import nullcontext

TABLES = {
    "youtube": "youtube_uploads",
    "tiktok": "tiktok_posts",
    "instagram": "instagram_posts",
    "facebook": "facebook_posts",
    "x": "x_posts",
}


def source_rights(db, recording_id):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='twitch_intake'"
    ).fetchone():
        return None
    return db.execute(
        "SELECT j.clip_id,j.options,r.expires,r.can_publish,r.can_share,a.editor_id,a.validated "
        "FROM twitch_intake j LEFT JOIN twitch_rights r ON r.broadcaster_id=j.broadcaster_id "
        "AND r.editor_id=j.editor_id LEFT JOIN twitch_editor a ON a.slot=1 AND a.editor_id=j.editor_id "
        "WHERE j.recording_id=?",
        (recording_id,),
    ).fetchone()


def active(source):
    stamp = time.time()
    return bool(
        source["editor_id"]
        and source["expires"]
        and source["expires"] > stamp
        and source["validated"] > stamp - 3600
    )


def processing_error(store, recording_id, *, ai=False):
    with store.connection() as db:
        source = source_rights(db, recording_id)
        if source and (not active(source) or (ai and not source["can_share"])):
            return "Current Twitch reuse rights do not permit this processing."
    return None


def autopilot_quality_error(db, source, clip):
    policy = json.loads(source["options"])
    if policy.get("autopilot_revision") is None:
        return None
    if (clip["duration"] or 0) < policy.get("minimum_seconds", 0):
        return "Finished clip is shorter than this run's minimum duration."
    if (
        policy.get("require_captions")
        and not db.execute(
            "SELECT 1 FROM clip_captions WHERE clip_id=? AND json_array_length(words)>0",
            (clip["id"],),
        ).fetchone()
    ):
        return "This run requires captions in the finished export."
    return None


def publishing_error(store, export_id, platform, account_id, job_id, *, reserve=False, db=None):
    table = TABLES[platform]
    context = store.connection() if db is None else nullcontext(db)
    with context as connection:
        if reserve:
            connection.execute("BEGIN IMMEDIATE")
        clip = connection.execute(
            "SELECT id,recording_id,duration FROM clips WHERE id=?", (str(export_id),)
        ).fetchone()
        source = source_rights(connection, clip["recording_id"]) if clip else None
        if not source:
            return None
        if not active(source) or not source["can_publish"]:
            return "Current Twitch reuse rights do not permit publishing this clip."
        if quality := autopilot_quality_error(connection, source, clip):
            return quality
        destination = "*" if platform == "x" else account_id
        key = (source["clip_id"], platform, destination)
        claim = connection.execute(
            "SELECT job_id FROM twitch_post_claims WHERE clip_id=? AND platform=? AND destination=?",
            key,
        ).fetchone()
        if claim and claim[0] != str(job_id):
            previous = connection.execute(
                f"SELECT status FROM {table} WHERE id=?", (claim[0],)
            ).fetchone()
            # Only a provider-confirmed failure allows a new claim. Never release a lost or
            # ambiguous handoff, even if the original project or export was removed.
            if not previous or previous["status"] != "failed":
                return "This Twitch clip already has a publishing attempt for this destination."
        if reserve:
            connection.execute(
                "INSERT INTO twitch_post_claims VALUES(?,?,?,?) ON CONFLICT(clip_id,platform,destination) "
                "DO UPDATE SET job_id=excluded.job_id",
                (*key, str(job_id)),
            )
    return None
