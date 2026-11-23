"""Validated project presets, atomic selected-clip actions and reversible edits."""

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .project_models import (
    ACTIVE,
    ItemEdit,
    Project,
    ProjectCreate,
    ProjectSettings,
    ProjectSettingsDraft,
)
from .storage import now


class SetupDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=120)
    settings: ProjectSettingsDraft
    recording_ids: list[UUID] = Field(default_factory=list, max_length=15)
    creation_token: UUID | None = None
    creation_signature: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$", max_length=64)


class PresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    workflow: Literal["long", "batch"]
    settings: ProjectSettings
    _name = field_validator("name")(ProjectCreate.title_text.__func__)

    @model_validator(mode="after")
    def workflow_settings(self):
        if (self.workflow == "batch") != (self.settings.duration_mode == "full"):
            raise ValueError("The preset must match its creation workflow.")
        return self


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    revision: int = Field(ge=0, strict=True)


class BulkAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targets: list[Target] = Field(min_length=1, max_length=75)
    action: Literal["posted", "review", "headline"]
    posted: bool | None = Field(default=None, strict=True)
    review: Literal["accepted", "rejected", "unreviewed"] | None = None
    headline_enabled: bool | None = Field(default=None, strict=True)
    headline_position: Literal["auto", "upper", "centre"] = "auto"
    opening_text: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def valid_action(self):
        if len({target.id for target in self.targets}) != len(self.targets):
            raise ValueError("Select each clip only once.")
        if (
            getattr(
                self,
                {"posted": "posted", "review": "review", "headline": "headline_enabled"}[
                    self.action
                ],
            )
            is None
        ):
            raise ValueError("Supply the setting required for this action.")
        return self


def create_workspace_router(manager):
    router = APIRouter()

    def item(project_id, item_id):
        project = manager.get(str(project_id), hydrate=False)
        value = next((i for i in project.items if i.id == str(item_id)), None)
        if value is None:
            raise HTTPException(404, "Project clip not found.")
        return project, value

    @router.post("/api/setup/validate", response_model=SetupDraft)
    def validate_draft(request: SetupDraft):
        return request

    @router.get("/api/project-presets")
    def presets():
        with manager.store.connection() as db:
            return [
                {
                    "id": row["id"],
                    **PresetRequest.model_validate_json(row["payload"]).model_dump(mode="json"),
                }
                for row in db.execute(
                    "SELECT * FROM project_presets ORDER BY name COLLATE NOCASE,id"
                )
            ]

    @router.post("/api/project-presets", status_code=201)
    def save_preset(request: PresetRequest):
        identifier = str(uuid4())
        request.settings.multimodal = False
        with manager.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if any(
                row[0].casefold() == request.name.casefold()
                for row in db.execute(
                    "SELECT name FROM project_presets WHERE workflow=?", (request.workflow,)
                )
            ):
                raise HTTPException(
                    409,
                    "A preset with this name already exists. Choose a different name or remove the old preset.",
                )
            if db.execute("SELECT count(*) FROM project_presets").fetchone()[0] >= 50:
                raise HTTPException(
                    409, "The preset library is full. Remove an unused preset first (maximum 50)."
                )
            db.execute(
                "INSERT INTO project_presets VALUES(?,?,?,?,?)",
                (identifier, request.name, request.workflow, request.model_dump_json(), now()),
            )
        return {"id": identifier, **request.model_dump(mode="json")}

    @router.delete("/api/project-presets/{identifier}")
    def delete_preset(identifier: UUID):
        with manager.store.connection() as db:
            if not db.execute(
                "DELETE FROM project_presets WHERE id=?", (str(identifier),)
            ).rowcount:
                raise HTTPException(404, "Preset not found.")
        return {"deleted": True}

    @router.post("/api/projects/{identifier}/bulk", response_model=Project, status_code=202)
    def bulk(identifier: UUID, request: BulkAction):
        with manager.lock:
            project = manager.get(str(identifier), hydrate=False)
            if project.status in ACTIVE:
                raise HTTPException(409, "Wait for processing or cancel it before changing clips.")
            targets = []
            for target in request.targets:
                value = next((i for i in project.items if i.id == str(target.id)), None)
                if value is None or value.revision != target.revision:
                    raise HTTPException(
                        409,
                        "A selected clip changed or was removed. Reload before applying changes.",
                    )
                targets.append(value)
            if request.action == "posted":
                with manager.store.connection() as db:
                    db.execute("BEGIN IMMEDIATE")
                    for value in targets:
                        value.posted = request.posted
                        value.revision += 1
                        for export_id in {value.export_id, value.pending_export_id} - {None}:
                            manager.store.set_posted(export_id, request.posted, db=db)
                    manager.records.save(project, db=db)
                return manager.get(str(identifier))
            edits = []
            for value in targets:
                data = {
                    key: getattr(value, key)
                    for key in ItemEdit.model_fields
                    if key != "auto_presentation"
                }
                data.update(
                    {"review": request.review}
                    if request.action == "review"
                    else {
                        "headline_enabled": request.headline_enabled,
                        "headline_position": request.headline_position,
                        "opening_text": request.opening_text,
                    }
                )
                try:
                    edits.append((value.id, ItemEdit.model_validate(data)))
                except ValidationError as exc:
                    raise HTTPException(
                        422,
                        "The headline conflicts with a selected clip's caption or overlay placement. No clips changed.",
                    ) from exc
            return manager.edit_many(str(identifier), edits)

    @router.get("/api/projects/{identifier}/clips/{item_id}/versions")
    def versions(identifier: UUID, item_id: UUID):
        item(identifier, item_id)
        values = manager.records.versions(str(identifier), str(item_id))
        identifiers = {value["export_id"] for value in values if value["export_id"]}
        available = set()
        if identifiers:
            with manager.store.connection() as db:
                available = {
                    row[0]
                    for row in db.execute(
                        f"SELECT id FROM clips WHERE status='succeeded' AND id IN ({','.join('?' for _ in identifiers)})",
                        tuple(identifiers),
                    )
                    if manager.store.export_path(row[0]).is_file()
                }
        for value in values:
            value.pop("payload")
            value["playable"] = value["export_id"] in available
        return values

    @router.post(
        "/api/projects/{identifier}/clips/{item_id}/restore",
        response_model=Project,
        status_code=202,
    )
    def restore(identifier: UUID, item_id: UUID, request: Target):
        with manager.lock:
            project, value = item(identifier, item_id)
            version = next(
                (
                    v
                    for v in manager.records.versions(project.id, value.id)
                    if v["id"] == str(request.id)
                ),
                None,
            )
            if version is None:
                raise HTTPException(404, "This version does not belong to this clip.")
            settings = ItemEdit.model_validate(
                {**version["settings"], "revision": request.revision}
            )
            return manager.edit_many(
                project.id,
                [(value.id, settings)],
                reuse_exports={value.id: version["export_id"]} if version["export_id"] else None,
            )

    return router
