"""Convert a pydantic model into a JSON Schema every provider accepts.

Providers each accept a different subset of JSON Schema: Claude rejects length
limits, OpenAI's strict mode needs every property listed as required, and Gemini
does not document ``$ref``. This keeps only the keywords common to all of them.
Size limits become plain text in ``description`` so the model still knows them,
and pydantic enforces them when it validates the output.
"""

from typing import Any

from pydantic import BaseModel

_COPIED_KEYWORDS = ("type", "description", "enum", "const")


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    return _convert(raw, raw.get("$defs", {}), ())


def _convert(node: dict[str, Any], defs: dict[str, Any], parents: tuple[str, ...]) -> dict[str, Any]:
    if "$ref" in node:
        name = node["$ref"].removeprefix("#/$defs/")
        if name in parents:
            raise ValueError(f"Recursive schemas are not supported: {name}")
        return _convert(defs[name], defs, (*parents, name))

    if "anyOf" in node:
        return {"anyOf": [_convert(option, defs, parents) for option in node["anyOf"]]}

    converted = {key: node[key] for key in _COPIED_KEYWORDS if key in node}
    if "properties" in node:
        properties = node["properties"]
        converted["properties"] = {
            name: _convert(value, defs, parents) for name, value in properties.items()
        }
        converted["required"] = list(properties)
        converted["additionalProperties"] = False
    if "items" in node:
        converted["items"] = _convert(node["items"], defs, parents)
    limits = _describe_limits(node)
    if limits:
        converted["description"] = " ".join(filter(None, (node.get("description"), limits)))
    return converted


def _describe_limits(node: dict[str, Any]) -> str:
    if "maxItems" in node:
        return f"At most {node['maxItems']} items."
    if "maxLength" in node:
        return f"At most {node['maxLength']} characters."
    return ""
