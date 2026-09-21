"""Validation du format JSON des synoptiques (schema 1)."""

import base64
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.api.synoptic_doc import (
    MAX_IMAGE_BYTES,
    WIDGET_TYPES,
    is_data_image,
    is_safe_css_value,
    is_valid_slug,
    slugify,
    validate_document,
)

POINT = str(uuid.uuid4())
# Cas partagés avec l'analyseur TypeScript du viewer (frontend/src/synoptic/expressions.ts).
EXPRESSIONS = json.loads(
    (
        Path(__file__).resolve().parents[2] / "frontend/src/synoptic/expression_cases.json"
    ).read_text()
)


def widget(**overrides: Any) -> dict[str, Any]:
    return {"id": "w1", "type": "value", "x": 10, "y": 20, "w": 160, "h": 48, **overrides}


def doc(*widgets: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return {"schema": 1, "name": "CTA 1", "widgets": list(widgets), **overrides}


def png(size: int = 100) -> str:
    return "data:image/png;base64," + base64.b64encode(b"x" * size).decode()


def errors(raw: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        validate_document(raw)
    return str(caught.value)


class TestValidDocuments:
    def test_the_example_of_the_specification_is_accepted(self) -> None:
        example = {
            "schema": 1,
            "name": "CTA 1",
            "canvas": {"width": 1920, "height": 1080, "background": "#0f172a", "bg_image": None},
            "widgets": [
                {
                    "id": "w1", "type": "value", "x": 320, "y": 140, "w": 160, "h": 48,
                    "bind": {"point": POINT, "format": "0.0", "unit": True},
                    "style": {"fontSize": 24, "color": "#ffffff"},
                    "rules": [
                        {"when": "value > 28", "style": {"color": "#ef4444"}},
                        {"when": "status != 'ok'", "style": {"opacity": 0.4}},
                    ],
                },
                {
                    "id": "w9", "type": "link", "x": 0, "y": 0, "w": 100, "h": 30,
                    "target": "synoptic:cta-2",
                },
            ],
            "links": [{"type": "navigate", "widget": "w9", "target": "synoptic:cta-2"}],
        }  # fmt: skip
        parsed = validate_document(example)
        assert parsed.name == "CTA 1" and len(parsed.widgets) == 2
        # Aller-retour : la version stockée garde "schema" et les propriétés propres au widget.
        stored = parsed.to_json()
        assert stored["schema"] == 1 and stored["widgets"][1]["target"] == "synoptic:cta-2"
        assert validate_document(stored).to_json() == stored

    def test_defaults_fill_an_empty_document(self) -> None:
        parsed = validate_document({"name": "Vide"})
        assert (parsed.canvas.width, parsed.canvas.height) == (1920, 1080)
        assert parsed.widgets == [] and parsed.links == []

    def test_every_widget_type_of_the_specification_is_known(self) -> None:
        expected = {
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
        assert expected == WIDGET_TYPES
        for kind in expected:
            validate_document(doc(widget(type=kind)))

    def test_trend_binds_up_to_eight_points(self) -> None:
        points = [{"point": str(uuid.uuid4())} for _ in range(8)]
        validate_document(doc(widget(type="trend", bind={"points": points})))
        assert "8 points" in errors(doc(widget(type="trend", bind={"points": points + points[:1]})))


class TestStructure:
    @pytest.mark.parametrize(
        ("raw", "fragment"),
        [
            ({"schema": 2, "name": "x"}, "schéma"),
            ({"name": ""}, "name"),
            ({"name": "x", "inconnu": 1}, "inconnu"),
            (doc(widget(id="a b")), "id"),
            (doc(widget(type="hologramme")), "type de widget"),
            (doc(widget(w=0)), "w"),
            (doc(widget(x=99999)), "x"),
            (doc(widget(), widget()), "uniques"),
            (
                doc(
                    widget(),
                    links=[{"type": "navigate", "widget": "absent", "target": "synoptic:a"}],
                ),
                "inexistant",
            ),
            (
                doc(
                    widget(), links=[{"type": "navigate", "widget": "w1", "target": "http://evil"}]
                ),
                "cible",
            ),
            (doc(widget(type="link", target="cta-2")), "cible"),
            (doc(canvas={"width": 10}), "width"),
            (doc(canvas={"height": 99999}), "height"),
        ],
    )
    def test_invalid_documents_are_refused(self, raw: dict[str, Any], fragment: str) -> None:
        assert fragment in errors(raw)

    def test_widget_and_rule_counts_are_bounded(self) -> None:
        many = [widget(id=f"w{i}") for i in range(501)]
        assert "500" in errors(doc(*many))
        rules = [{"when": "value > 1", "style": {}} for _ in range(21)]
        assert "20" in errors(doc(widget(rules=rules)))

    def test_bound_points_must_be_uuids(self) -> None:
        assert "identifiant de point" in errors(doc(widget(bind={"point": "n'importe quoi"})))
        assert "identifiant de point" in errors(
            doc(widget(type="trend", bind={"points": [{"point": "x"}]}))
        )


class TestRulesAndStyles:
    def test_rule_expressions_must_follow_the_grammar(self) -> None:
        for good in EXPRESSIONS["valid"]:
            validate_document(doc(widget(rules=[{"when": good, "style": {"color": "red"}}])))
        for bad in EXPRESSIONS["invalid"]:
            assert "when" in errors(doc(widget(rules=[{"when": bad, "style": {}}]))), bad
        assert "200" in errors(doc(widget(rules=[{"when": "v" * 201, "style": {}}])))

    @pytest.mark.parametrize(
        "value",
        [
            "url(https://evil/x)",
            "URL(x)",
            "red; background: url(x)",
            "red}body{x:y",
            "expression(alert(1))",
            "javascript:alert(1)",
            "<b>",
            "@import x",
            "x" * 101,
        ],
    )
    def test_dangerous_css_values_are_refused_in_styles_and_rules(self, value: str) -> None:
        assert not is_safe_css_value(value)
        assert "refusée" in errors(doc(widget(style={"color": value})))
        assert "refusée" in errors(
            doc(widget(rules=[{"when": "value > 1", "style": {"background": value}}]))
        )
        assert "refusé" in errors(doc(canvas={"background": value})) or "invalide" in errors(
            doc(canvas={"background": value})
        )

    def test_only_known_style_properties_are_accepted(self) -> None:
        assert "inconnue" in errors(doc(widget(style={"position": "fixed"})))
        assert "inconnue" in errors(
            doc(widget(rules=[{"when": "value > 1", "style": {"zIndex": 9}}]))
        )
        validate_document(
            doc(widget(style={"color": "#fff", "opacity": 0.5, "fontSize": 24, "strokeWidth": 3}))
        )

    def test_css_values_helper(self) -> None:
        assert (
            is_safe_css_value("#ef4444")
            and is_safe_css_value("rgba(0,0,0,.5)")
            and is_safe_css_value(0.4)
        )
        assert (
            not is_safe_css_value(True)
            and not is_safe_css_value(None)
            and not is_safe_css_value(["x"])
        )


class TestImages:
    def test_data_images_are_accepted_within_the_size_limit(self) -> None:
        assert is_data_image(png(1000))
        assert is_data_image("data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode())
        validate_document(doc(canvas={"bg_image": png()}))
        validate_document(doc(widget(type="image", src=png())))

    @pytest.mark.parametrize(
        "value",
        [
            "https://evil/x.png",
            "data:text/html;base64,PGh0bWw+",
            "data:image/gif;base64,AAAA",
            "data:image/png,not-base64",
            "javascript:alert(1)",
            "data:image/png;base64,***",
        ],
    )
    def test_anything_else_is_refused(self, value: str) -> None:
        assert not is_data_image(value)
        assert "image" in errors(doc(canvas={"bg_image": value}))
        assert "image" in errors(doc(widget(type="image", src=value)))

    def test_images_over_5_mb_are_refused(self) -> None:
        assert is_data_image(png(MAX_IMAGE_BYTES - 10))
        assert not is_data_image(png(MAX_IMAGE_BYTES + 100))


class TestSlugs:
    @pytest.mark.parametrize(
        ("name", "slug"),
        [
            ("CTA 1", "cta-1"),
            ("Salle serveurs - étage 2", "salle-serveurs-etage-2"),
            ("  ---  ", "synoptique"),
            ("Ça/va très bien !", "ca-va-tres-bien"),
        ],
    )
    def test_slugify(self, name: str, slug: str) -> None:
        assert slugify(name) == slug and is_valid_slug(slug)

    def test_valid_slugs(self) -> None:
        assert is_valid_slug("cta-2") and is_valid_slug("a")
        for bad in ("", "CTA", "a--b", "-a", "a-", "a b", "a_b", "é", "x" * 65):
            assert not is_valid_slug(bad)
