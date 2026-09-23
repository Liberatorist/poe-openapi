"""
Manual corrections for fields where the official PoE API docs describe the
wrong type, or omit a field entirely. Each entry replaces (or adds) the
generated schema for schemas[schema_name].properties[property_name] with the
given fragment, preserving any existing description.
"""

SCHEMA_OVERRIDES = {
    # Docs claim these are integers, but the API actually returns them as
    # quoted strings (e.g. "12345").
    ("PassiveNode", "skill"): {"type": "string"},
    ("PassiveNodeExpansionJewel", "proxy"): {"type": "string"},
    ("PassiveNodeExpansionJewel", "parent"): {"type": "string"},
    # Docs omit this field entirely, but the API always returns it.
    ("MercenarySkillSupport", "icon"): {"type": "string"},
}

# Fields the official docs fail to mark as required, even though the API
# always returns them.
EXTRA_REQUIRED_FIELDS = {
    "MercenarySkillSupport": ["icon"],
}


def apply_schema_overrides(openapi):
    schemas = openapi["components"]["schemas"]
    for (schema_name, prop_name), fix in SCHEMA_OVERRIDES.items():
        schema = schemas.get(schema_name)
        if not schema:
            continue
        prop = schema.setdefault("properties", {}).setdefault(prop_name, {})
        description = prop.get("description")
        prop.clear()
        prop.update(fix)
        if description and "description" not in prop:
            prop["description"] = description

    for schema_name, extra_required in EXTRA_REQUIRED_FIELDS.items():
        schema = schemas.get(schema_name)
        if not schema:
            continue
        required = set(schema.get("required", []))
        required.update(extra_required)
        schema["required"] = sorted(required)
