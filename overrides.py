"""
Manual corrections for fields where the official PoE API docs describe the
wrong type. Each entry replaces the generated schema for
schemas[schema_name].properties[property_name] with the given fragment,
preserving any existing description.
"""

SCHEMA_OVERRIDES = {
    # Docs claim these are integers, but the API actually returns them as
    # quoted strings (e.g. "12345").
    ("PassiveNode", "skill"): {"type": "string"},
    ("CrucibleNode", "skill"): {"type": "string"},
    ("PassiveGroup", "proxy"): {"type": "string"},
    ("PassiveNodeExpansionJewel", "proxy"): {"type": "string"},
}


def apply_schema_overrides(openapi):
    schemas = openapi["components"]["schemas"]
    for (schema_name, prop_name), fix in SCHEMA_OVERRIDES.items():
        schema = schemas.get(schema_name)
        if not schema:
            continue
        prop = schema.get("properties", {}).get(prop_name)
        if prop is None:
            continue
        description = prop.get("description")
        prop.clear()
        prop.update(fix)
        if description and "description" not in prop:
            prop["description"] = description
