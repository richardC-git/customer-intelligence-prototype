from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from sfs_intelligence.llm.schema import strict_schema
from sfs_intelligence.models.competitor import CompetitorAnalysis
from sfs_intelligence.models.prospect import ProspectAnalysis
from sfs_intelligence.research.follow_up import ResearchPlan

# The keywords shared by the OpenAI, Anthropic, and Gemini schema subsets.
PORTABLE_KEYWORDS = {
    "type",
    "description",
    "enum",
    "const",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "anyOf",
}


def walk(node):
    yield node
    for child in node.get("properties", {}).values():
        yield from walk(child)
    if "items" in node:
        yield from walk(node["items"])
    for option in node.get("anyOf", ()):
        yield from walk(option)


def test_prospect_analysis_uses_only_portable_keywords():
    schema = strict_schema(ProspectAnalysis)

    for node in walk(schema):
        assert set(node) <= PORTABLE_KEYWORDS


def test_every_object_is_closed_and_lists_all_properties():
    objects = [node for node in walk(strict_schema(ProspectAnalysis)) if "properties" in node]

    assert len(objects) >= 3
    for node in objects:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])


def test_references_are_inlined():
    assert "$ref" not in str(strict_schema(ProspectAnalysis))
    assert "$defs" not in strict_schema(ProspectAnalysis)


def test_root_is_an_object():
    assert strict_schema(ProspectAnalysis)["type"] == "object"


def test_limits_become_descriptions_so_the_model_knows_them():
    class Limited(BaseModel):
        name: Annotated[str, Field(min_length=2, max_length=4, pattern="^a")]
        items: Annotated[tuple[str, ...], Field(max_length=5, description="Things.")]

    properties = strict_schema(Limited)["properties"]

    assert properties["name"] == {"type": "string", "description": "At most 4 characters."}
    assert properties["items"]["description"] == "Things. At most 5 items."
    assert "maxItems" not in properties["items"]
    with pytest.raises(ValueError):
        Limited(name="abcdefgh")


def test_every_limit_on_the_analysis_models_reaches_the_model():
    for model in (ProspectAnalysis, CompetitorAnalysis, ResearchPlan):
        schema = strict_schema(model)
        for name, field in model.model_fields.items():
            if any(type(item).__name__ == "MaxLen" for item in field.metadata):
                assert "At most" in schema["properties"][name]["description"], name


def test_optional_fields_keep_their_null_option():
    class Optional(BaseModel):
        value: str | None = None

    schema = strict_schema(Optional)

    assert schema["properties"]["value"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert schema["required"] == ["value"]


def test_recursive_models_are_rejected():
    class Node(BaseModel):
        child: "Node | None" = None

    with pytest.raises(ValueError, match="Recursive"):
        strict_schema(Node)
