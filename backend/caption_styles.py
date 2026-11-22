"""Local reusable caption styles; content-specific emphasis stays with each clip."""

import sqlite3
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .captions import CaptionCustomization, CaptionPreset
from .transcription_models import clean_text


class CaptionStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: CaptionPreset | Literal["none"] = "classic"
    position: Literal["auto", "lower", "centre", "upper"] = "lower"
    customization: CaptionCustomization = Field(default_factory=CaptionCustomization)


class StyleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60, strict=True)
    style: CaptionStyle

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = clean_text(value)
        if not value:
            raise ValueError("Style name cannot be blank.")
        return value


class SavedStyle(StyleCreate):
    id: UUID


class StyleTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    revision: int = Field(ge=0, strict=True)


class BatchStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targets: list[StyleTarget] = Field(min_length=1, max_length=75)
    style: CaptionStyle

    @model_validator(mode="after")
    def unique_targets(self):
        if len({target.id for target in self.targets}) != len(self.targets):
            raise ValueError("Choose each clip only once.")
        return self


class CaptionStyles:
    def __init__(self, store):
        self.store = store

    def initialize(self):
        with self.store.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS caption_styles (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)"
            )

    def list(self) -> list[SavedStyle]:
        with self.store.connection() as db:
            return [
                SavedStyle(
                    id=row["id"],
                    name=row["name"],
                    style=CaptionStyle.model_validate_json(row["payload"]),
                )
                for row in db.execute("SELECT * FROM caption_styles ORDER BY name_key,id")
            ]

    def save(self, request: StyleCreate) -> SavedStyle:
        identifier = uuid4()
        try:
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT COUNT(*) FROM caption_styles").fetchone()[0] >= 50:
                    raise HTTPException(
                        409, "The 50-style limit is reached. Delete an unused style first."
                    )
                db.execute(
                    "INSERT INTO caption_styles VALUES (?,?,?,?)",
                    (
                        str(identifier),
                        request.name,
                        request.name.casefold(),
                        request.style.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "A caption style with that name already exists.") from exc
        return SavedStyle(id=identifier, **request.model_dump())

    def delete(self, identifier: UUID):
        with self.store.connection() as db:
            if not db.execute("DELETE FROM caption_styles WHERE id=?", (str(identifier),)).rowcount:
                raise HTTPException(404, "Caption style not found.")


def create_caption_style_router(styles, projects):
    router = APIRouter(prefix="/api")

    @router.get("/caption-styles", response_model=list[SavedStyle])
    def list_styles():
        return styles.list()

    @router.post("/caption-styles", response_model=SavedStyle, status_code=201)
    def save_style(request: StyleCreate):
        return styles.save(request)

    @router.delete("/caption-styles/{identifier}")
    def delete_style(identifier: UUID):
        styles.delete(identifier)
        return {"deleted": True}

    @router.post("/projects/{identifier}/caption-style", status_code=202)
    def batch_style(identifier: UUID, request: BatchStyle):
        return projects.apply_caption_style(str(identifier), request)

    return router
