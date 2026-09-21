"""Format JSON d'un synoptique (schema 1, section 10.2) et sa validation.

Le document est stocké tel quel dans `synoptic_version.json`. La validation vérifie la structure et
les valeurs que le viewer interprète (couleurs, expressions de règles, images) ; les propriétés
propres à un type de widget restent libres (`extra="allow"`) : elles évoluent avec les widgets.
"""

from __future__ import annotations

import base64
import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.synoptic_expr import ExpressionError, check_expression

SCHEMA_VERSION = 1
MAX_WIDGETS = 500
MAX_RULES = 20
MAX_DOC_BYTES = 6 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # fond du canevas et pictogrammes (section 10.4)

WIDGET_TYPES = frozenset(
    {
        "value",
        "label",
        "gauge",
        "indicator",
        "switch",
        "setpoint",
        "trend",
        "alarm_list",
        "image",
        "link",
        "shape",
    }
)
# Propriétés de style que les règles et le viewer savent appliquer.
STYLE_KEYS = frozenset(
    {
        "color",
        "background",
        "opacity",
        "fontSize",
        "fontWeight",
        "textAlign",
        "fill",
        "stroke",
        "strokeWidth",
        "borderColor",
        "borderRadius",
    }
)

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TARGET = re.compile(r"^synoptic:([a-z0-9]+(-[a-z0-9]+)*)$")
_DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|svg\+xml|webp);base64,([A-Za-z0-9+/=]+)$")


class DocumentError(ValueError):
    """Document invalide ; le message est destiné à l'utilisateur."""


def is_safe_css_value(value: Any) -> bool:
    """Interdit ce qui déclencherait une requête ou sortirait de la déclaration (`url(`, `;`...)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    if not isinstance(value, str) or len(value) > 100:
        return False
    lowered = value.lower()
    if any(token in lowered for token in ("url(", "expression", "@import", "javascript:")):
        return False
    return not any(char in value for char in ";{}<>\\")


def is_data_image(value: str) -> bool:
    match = _DATA_IMAGE.match(value)
    if match is None:
        return False
    # Taille décodée approchée sans décoder : 3 octets pour 4 caractères.
    return len(match.group(2)) * 3 // 4 <= MAX_IMAGE_BYTES


class Canvas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=1920, ge=320, le=8000)
    height: int = Field(default=1080, ge=240, le=8000)
    background: str = "#0f172a"
    bg_image: str | None = None

    @field_validator("background")
    @classmethod
    def _background(cls, value: str) -> str:
        if not is_safe_css_value(value):
            raise ValueError("couleur de fond invalide")
        return value

    @field_validator("bg_image")
    @classmethod
    def _bg_image(cls, value: str | None) -> str | None:
        if value is not None and not is_data_image(value):
            raise ValueError("image de fond : PNG, JPEG, WebP ou SVG en data URL, 5 Mo au plus")
        return value


class Rule(BaseModel):
    """Règle d'affichage : quand l'expression est vraie, le style est appliqué (section 10.2)."""

    model_config = ConfigDict(extra="forbid")

    when: str = Field(min_length=1, max_length=200)
    style: dict[str, Any] = Field(default_factory=dict)

    @field_validator("when")
    @classmethod
    def _when(cls, value: str) -> str:
        try:
            check_expression(value)
        except ExpressionError as exc:
            raise ValueError(f"expression de règle invalide : {exc}") from exc
        return value

    @field_validator("style")
    @classmethod
    def _style(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_style(value)


def _check_style(style: dict[str, Any]) -> dict[str, Any]:
    for key, item in style.items():
        if key not in STYLE_KEYS:
            raise ValueError(f"propriété de style inconnue : {key}")
        if not is_safe_css_value(item):
            raise ValueError(f"valeur de style refusée pour {key}")
    return style


def _check_uuid(value: Any, where: str) -> None:
    try:
        uuid.UUID(str(value))
    except ValueError:
        raise ValueError(f"{where} : identifiant de point invalide") from None


class Widget(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    x: float = Field(ge=-10000, le=20000)
    y: float = Field(ge=-10000, le=20000)
    w: float = Field(ge=1, le=20000)
    h: float = Field(ge=1, le=20000)
    bind: dict[str, Any] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list, max_length=MAX_RULES)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID.match(value):
            raise ValueError("id : 1 à 64 caractères (lettres, chiffres, _ et -)")
        return value

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        if value not in WIDGET_TYPES:
            raise ValueError(f"type de widget inconnu : {value}")
        return value

    @field_validator("style")
    @classmethod
    def _style(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_style(value)

    @model_validator(mode="after")
    def _properties(self) -> Widget:
        where = f"widget {self.id}"
        if (point := self.bind.get("point")) is not None:
            _check_uuid(point, where)
        for entry in self.bind.get("points", []):
            _check_uuid(entry.get("point") if isinstance(entry, dict) else entry, where)
        if len(self.bind.get("points", [])) > 8:
            raise ValueError(f"{where} : 8 points au plus par courbe")
        extra = self.model_extra or {}
        for key in ("src",):
            if key in extra and (not isinstance(extra[key], str) or not is_data_image(extra[key])):
                raise ValueError(
                    f"{where} : image en data URL (PNG, JPEG, WebP, SVG), 5 Mo au plus"
                )
        target = extra.get("target")
        if target is not None and not (isinstance(target, str) and _TARGET.match(target)):
            raise ValueError(f"{where} : cible de navigation invalide (synoptic:<slug>)")
        return self


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["navigate"]
    widget: str
    target: str

    @field_validator("target")
    @classmethod
    def _target(cls, value: str) -> str:
        if not _TARGET.match(value):
            raise ValueError("cible de navigation invalide (synoptic:<slug>)")
        return value


class SynopticDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Annotated[int, Field(alias="schema")] = SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=255)
    canvas: Canvas = Field(default_factory=Canvas)
    widgets: list[Widget] = Field(default_factory=list, max_length=MAX_WIDGETS)
    links: list[Link] = Field(default_factory=list, max_length=MAX_WIDGETS)

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"version de schéma non prise en charge : {value}")
        return value

    @model_validator(mode="after")
    def _references(self) -> SynopticDoc:
        ids = [w.id for w in self.widgets]
        if len(ids) != len(set(ids)):
            raise ValueError("les id de widgets doivent être uniques")
        for link in self.links:
            if link.widget not in ids:
                raise ValueError(f"lien vers un widget inexistant : {link.widget}")
        return self

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


def validate_document(raw: dict[str, Any]) -> SynopticDoc:
    """Valide un document ; lève `pydantic.ValidationError` avec des messages exploitables."""
    return SynopticDoc.model_validate(raw)


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG.match(slug)) and len(slug) <= 64


def slugify(name: str) -> str:
    """`Salle serveurs - étage 2` -> `salle-serveurs-etage-2`."""
    import unicodedata

    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:64].strip("-")
    return slug or "synoptique"


def decoded_image_size(data_url: str) -> int:
    match = _DATA_IMAGE.match(data_url)
    return len(base64.b64decode(match.group(2))) if match else 0
