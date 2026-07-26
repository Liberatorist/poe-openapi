import json
import re
import yaml
from bs4 import BeautifulSoup
from itertools import product
from urllib.request import Request, urlopen
import requests


primitive_translations = {
    "string": {"type": "string"},
    "uint": {"type": "integer"},
    "double":  {"type": "number", "format": "double"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "int": {"type": "integer"},
    "Error": {"type": "object", "properties": {"code":  {"type": "integer", "enum": [200, 202, 400, 404, 429, 500]}, "message": {"type": "string"}}},
}

http_verbs = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}

# realm -> path segment (None means the realm is omitted from the path)
realms = {
    "pc": None,
    "xbox": "xbox",
    "sony": "sony",
    "poe2": "poe2",
}

def fetch_latest_versions() -> tuple[str, str]:
    """
    Fetch the latest PoE1 and PoE2 versions from the top entries of the changelog.
    Entries look like "3.28.0:" for PoE1 and "PoE2 EA - 0.5.0:" for PoE2.
    """
    soup = fetch_soup("https://www.pathofexile.com/developer/docs/changelog")
    poe1_version = poe2_version = None
    for h3 in soup.find_all("h3"):
        match = re.match(r"^(PoE2 EA - )?(\d+\.\d+(?:\.\d+)?):", h3.text.strip())
        if not match:
            continue
        if match.group(1):
            poe2_version = poe2_version or match.group(2)
        else:
            poe1_version = poe1_version or match.group(2)
        if poe1_version and poe2_version:
            break
    if not poe1_version or not poe2_version:
        raise RuntimeError("Could not determine latest versions from changelog")
    return poe1_version, poe2_version


def toCamelCase(string: str) -> str:
    """
    Convert a string to camelCase.
    """
    parts = string.split(" ")
    return ''.join(part.capitalize() for part in parts)


def find_all_before(start, tag_name, limit_tag):
    """
    Find all elements before the next occurrence of a specific tag.
    """
    elements = []
    for sib in start.find_all_next():
        if sib.name == limit_tag:
            break
        if sib.name == tag_name:
            elements.append(sib)
    return elements


def find_next_before(start, tag_name, limit_tag, content):
    """
    Find the next occurrence of a specific tag before another tag.
    """
    for sib in start.find_all_next():
        if sib.name == limit_tag:
            break
        if sib.name == tag_name and content in sib.get_text(strip=True):
            return sib
    return None


def handle_type(type_name, trs: list, level: int = 0) -> tuple[dict, bool]:
    """
    Handle a type name and return its OpenAPI schema representation.
    """
    schema = {}
    req = True
    if type_name.startswith("?"):
        req = False
        type_name = type_name[1:]
    type_name = type_name.strip()
    if " as " in type_name:
        # ignore enums for now
        type_name = type_name.split(" as ")[0].strip()

    if " or " in type_name:
        parts = type_name.split(" or ")
        schema["oneOf"] = []
        for part in parts:
            part = part.strip()
            if part.startswith("?"):
                part = part[1:].strip()
                req = True
            sub_prop, sub_req = handle_type(part, trs, level)
            schema["oneOf"].append(sub_prop)
            if sub_req:
                req = True
        return schema, req

    if type_name in primitive_translations:
        schema.update(primitive_translations[type_name])
    elif type_name.startswith("array of "):
        child_type = type_name[9:].strip()
        schema["type"] = "array"
        child_schema, _ = handle_type(child_type, trs, level)
        schema["items"] = child_schema
    elif type_name == "array":
        schema["type"] = "array"
        schema["items"] = {"oneOf": []}
        number_of_items = 0
        for tr in trs:
            cells = tr.find_all("td")
            if len(cells) < 2:
                continue
            key = cells[0].text.strip()
            if key[level * 2] != "↳":
                break
            number_of_items += 1
            key = key[2:].strip()
            value_type = " ".join([c.text.strip() for c in cells[1].children])
            child_schema, r = handle_type(value_type, [])
            schema["items"]["oneOf"].append(child_schema)

        schema["minItems"] = number_of_items
        schema["maxItems"] = number_of_items
    elif type_name.startswith("dictionary of "):
        child_type = type_name[14:].strip()
        schema["type"] = "object"
        child_schema, _ = handle_type(child_type, trs)
        schema["additionalProperties"] = child_schema
    elif type_name == "object":
        schema["type"] = "object"
        schema["properties"] = {}
        required = set()
        for idx, tr in enumerate(trs):
            cells = tr.find_all("td")
            if len(cells) < 2:
                continue
            key = cells[0].text
            if key[level * 2] != "↳":
                break
            key = key.strip()[2:]
            value_type = " ".join([c.text.strip() for c in cells[1].children])
            child_schema, r = handle_type(value_type, trs[1 + idx:], level + 1)
            parse_third_column(cells, child_schema)
            schema["properties"][key] = child_schema
            if r:
                required.add(key)
        if required:
            schema["required"] = sorted(required)
    else:
        schema["$ref"] = f"#/components/schemas/{type_name}"
    return schema, req


def parse_table(table):
    table_schema = {"type": "object"}
    properties = {}
    required = set()
    trs = list(table.find_all("tr"))
    for i, tr in enumerate(trs):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        key = cells[0].text.strip()
        if key.startswith("↳"):
            continue
        value_type = " ".join([c.text.strip() for c in cells[1].children])
        field_schema, req = handle_type(value_type, trs[i+1:])
        parse_third_column(cells, field_schema)
        properties[key] = field_schema
        if req:
            required.add(key)
    table_schema["properties"] = properties
    if required:
        table_schema["required"] = sorted(required)
    return table_schema


def parse_third_column(cells, schema):
    if len(cells) > 2:
        if cells[2].text.strip() == "date time (ISO8601)":
            schema["format"] = "date-time"
        enums = list(cells[2].find_all("code"))
        if enums and "e.g." not in cells[2].text:
            if schema.get("type") == "string":
                schema["enum"] = [e.text.strip() for e in enums]
            elif schema.get("type") == "boolean":
                pass
                # properties[key]["enum"] = [
                #     e.text.strip().lower() == "true" for e in enums]
        description = cells[2].text.strip()
        if description:
            schema["description"] = description


def fetch_soup(url):
    response = requests.get(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    })
    return BeautifulSoup(response.text, "html.parser")


def build_openapi(soup, realm):
    openapi = {
            "openapi": "3.1.0",
            "info": dict(realm_info[realm]),
            "paths": {},
            "servers": [
                {
                    "url": "https://api.pathofexile.com"
                }
            ],
            "components": {
                "schemas": {
                    "Realm": {
                        "type": "string",
                        "enum": sorted(realms.keys()),
                    },
                },
                "parameters": {
                    "UserAgent": {
                        "name": "User-Agent",
                        "in": "header",
                        "required": True,
                        "schema": {
                            "type": "string"
                        },
                        "description": "format: OAuth {$clientId}/{$version} (contact: {$contact})"
                    }
                },
                "securitySchemes": {
                    "service": {
                        "type": "oauth2",
                        "flows": {
                            "clientCredentials": {
                                "tokenUrl": "https://www.pathofexile.com/oauth/token",
                                "scopes": {
                                    "service:leagues": "for fetching leagues.",
                                    "service:leagues:ladder": "for fetching league ladders.",
                                    "service:pvp_matches": "for fetching PvP matches.",
                                    "service:pvp_matches:ladder": "for fetching PvP match ladders.",
                                    "service:psapi": "for access to the Public Stash API.",
                                }
                            }
                        }
                    },
                    "account": {
                        "type": "oauth2",
                        "flows": {
                            "authorizationCode": {
                                "tokenUrl": "https://www.pathofexile.com/oauth/token",
                                "authorizationUrl": "https://www.pathofexile.com/oauth/authorize",
                                "scopes": {
                                    "account:profile": "for access to the account's basic profile information.",
                                    "account:leagues": "for viewing the account's available leagues (including private leagues).",
                                    "account:stashes": "for viewing the account's stashes and items.",
                                    "account:characters": "for viewing the account's characters and inventories.",
                                    "account:league_accounts": "for viewing the account's allocated atlas passives.",
                                    "account:item_filter": "for managing the account's item filters.",
                                }
                            }
                        }
                    },
                    "bearerAuth": {
                        "type": "http",
                        "scheme": "bearer",
                    }
                },
            },
            "tags": []
        }
    h2s = soup.find_all("h2")
    schemas = openapi["components"]["schemas"]
    tags = openapi["tags"] = []
    for h2 in h2s:
        tag = ""
        scope = ""
        section_title = h2.text.strip()
        poe1_only = "(PoE1 only)" in section_title
        section_title = section_title.replace("(PoE1 only)", "").strip()
        skip_endpoints = poe1_only and realm == "poe2"
        scopedivs = find_all_before(h2, "div", "h2")
        if scopedivs:
            if "scope" in scopedivs[0].text.lower():
                x = scopedivs[0].find_next("a")
                if x:
                    scope = x.text.strip()
                tag = section_title
                if not skip_endpoints:
                    tags.append({
                        "name": section_title,
                    })
        for h3 in find_all_before(h2, "h3", "h2"):
            text = h3.text.strip()
            if text.startswith("object"):
                table = h3.find_next("table")
                if table:
                    schemas[text.split(" ")[1]] = parse_table(table)
            elif not skip_endpoints:
                parse_endpoint(openapi, tag, scope, h3, realm)

    return openapi


def parse_endpoint(openapi, tag, scope, h3, realm):
    endpoint = h3.find_next("code")
    if not endpoint:
        return
    endpoint_text = endpoint.text.strip()
    summary = h3.text.strip().split(" (")[0].strip()
    verbmatch = [v for v in http_verbs if endpoint_text.startswith(v)]
    if not verbmatch:
        return
    http_verb = verbmatch[0]
    parameters = []
    optional_path_params = []
    pathParts = []
    param_names = []
    # Parse the endpoint path and fill pathParts and optional_path_params
    path = endpoint_text[len(http_verb):].strip()
    has_realm = "<realm>" in path
    if has_realm and realm != "pc":
        # skip endpoints that don't support this realm
        realm_li = find_next_before(h3, "li", "h3", "realm")
        if realm_li and realms[realm] not in realm_li.get_text():
            return
    for part in path.split("/"):
        part = part.strip().replace("[", "")
        if part.startswith("<") and part.endswith(">"):
            pname = part[1:-1]
            if pname == "realm":
                if realms[realm]:
                    pathParts.append(realms[realm])
                continue
            pathParts.append("{" + pname + "}")
            parameters.append({
                "name": pname,
                "in": "path",
                "required": True,
                "schema": {"type": "string"}
            })
            param_names.append(pname)
        elif part.endswith("]"):
            pname = part[1:-2]
            if pname == "realm":
                if realms[realm]:
                    pathParts.append(realms[realm])
                continue
            optional_path_params.append(pname)
            pathParts.append("{" + pname + "}")
            param_names.append(pname)
        else:
            pathParts.append(part)
    returnDef = find_next_before(
        h3, "h4", "h3", "Returns:")
    bodyDef = find_next_before(
        h3, "h4", "h3", "Request Body Parameters (JSON):")
    queryDef = find_next_before(
        h3, "h4", "h3", "Optional Query Parameters:")

    returnSchema = {"type": "object"}
    if returnDef:
        table = returnDef.find_next("table")
        if table:
            returnSchema = parse_table(table)

    requestBody = None
    if bodyDef:
        bodyTable = bodyDef.find_next("table")
        if bodyTable:
            requestBody = {
                "name": "body",
                "in": "body",
                "required": True,
                "schema": parse_table(bodyTable)
            }
    if queryDef:
        queryList = queryDef.find_next("ul")
        for li in queryList.find_all("li", recursive=False):
            param = {"description": ""}
            description = []
            children = list(li.children)
            c = li.find_next("code")
            param["name"] = c.text.strip().replace("=", "")
            param["in"] = "query"
            param["required"] = False
            param["schema"] = {"type": "string"}
            if param["name"] == "realm":
                if realm == "pc":
                    # pc is the default realm, no need for the parameter
                    continue
                if realms[realm] not in li.get_text():
                    # endpoint does not support this realm
                    return
                param["required"] = True
                param["schema"] = {"$ref": "#/components/schemas/Realm"}
            for child in children[1:]:
                description.append(child.text.strip())
            if description:
                param["description"] = " ".join(description).strip().replace("=", "")
            parameters.append(param)

    # Build the operation definition before assigning to paths
    definition = {
        "summary": summary,
        "operationId": toCamelCase(summary),
        "responses": {
            "200": {
                "description": "Successful response",
                "content": {
                    "application/json": {"schema":  returnSchema}
                }
            }
        },
        "security": [{"bearerAuth": []}],
    }
    if tag:
        definition["tags"] = [tag]
    if requestBody:
        definition["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": requestBody["schema"]
                }
            }
        }
    if scope:
        if "service" in scope:
            definition["security"].append({"service": [scope]})
        elif "account" in scope:
            definition["security"].append({"account": [scope]})

    # Generate all combinations of optional path params (powerset, except empty set)
    n = len(optional_path_params)
    if n == 0:
        # No optional params, just one endpoint
        path = "/".join(pathParts)
        if path not in openapi["paths"]:
            openapi["paths"][path] = {}
        if parameters:
            definition["parameters"] = parameters
        openapi["paths"][path][http_verb.lower()] = definition
    else:
        # For each combination, include/exclude each optional param
        for mask in product([False, True], repeat=n):
            # At least one True in mask (at least one optional param included)
            # But OpenAPI allows the empty set (all optional omitted) as a valid path, so include all
            new_path_parts = []
            new_parameters = []
            opt_idx = 0
            for part in pathParts:
                if part.startswith("{") and part[1:-1] in optional_path_params:
                    if mask[opt_idx]:
                        new_path_parts.append(part)
                        new_parameters.append({
                            "name": part[1:-1],
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"}
                        })
                    opt_idx += 1
                else:
                    new_path_parts.append(part)
                    if part.startswith("{"):
                        pname = part[1:-1]
                        if pname not in optional_path_params:
                            new_parameters.append({
                                "name": pname,
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"}
                            })
            new_path = "/".join([p for p in new_path_parts if p])
            if not new_path.startswith("/"):
                new_path = "/" + new_path
            if new_path not in openapi["paths"]:
                openapi["paths"][new_path] = {}
            def_copy = definition.copy()
            if new_parameters:
                def_copy["parameters"] = new_parameters

            opt_suffix = " ".join([
                f"{optional_path_params[i]}" for i, present in enumerate(mask) if present
            ])
            def_copy["operationId"] = toCamelCase(
                summary + " " + opt_suffix)
            def_copy["summary"] = summary + (
                f" (with optional: {', '.join([optional_path_params[i] for i, present in enumerate(mask) if present])})" if any(mask) else "")
            openapi["paths"][new_path][http_verb.lower()
                                       ] = def_copy


def apply_go_type_overrides(openapi):
    """
    oapi-codegen can't infer a Go type for fixed-size tuple arrays
    (e.g. ItemProperty.values, whose entries are [name, value] pairs),
    so it falls back to `interface{}`. Hoist that tuple schema into a
    named ItemPropertyValue component tagged with x-go-type so
    downstream oapi-codegen consumers can supply their own ItemValue
    type instead.
    """
    item_property = openapi["components"]["schemas"].get("ItemProperty")
    if not item_property:
        return
    values_schema = item_property.get("properties", {}).get("values")
    if not values_schema or "items" not in values_schema:
        return
    tuple_schema = values_schema["items"]
    tuple_schema["x-go-type"] = "ItemValue"
    openapi["components"]["schemas"]["ItemPropertyValue"] = tuple_schema
    values_schema["items"] = {"$ref": "#/components/schemas/ItemPropertyValue"}


def hoist_object_schemas(openapi):
    """
    Replace inline object schemas (type: object with properties) that
    appear nested inside other schemas or endpoint bodies with $ref
    pointers to named schemas under components/schemas, so callers get
    a reusable, referenceable type instead of a repeated anonymous
    object literal (e.g. AtlasPassiveTree instead of an inline object
    inside the atlas_passive_trees array).
    """
    schemas = openapi["components"]["schemas"]
    existing_by_shape = {
        json.dumps(schema, sort_keys=True): name for name, schema in schemas.items()
    }

    def unique_name(hint):
        base = "".join(
            part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", hint) if part
        ) or "Object"
        name = base
        i = 2
        while name in schemas:
            name = f"{base}{i}"
            i += 1
        return name

    def hoist(node, hint):
        if isinstance(node, dict):
            if "$ref" in node:
                return
            if "items" in node:
                item_hint = hint[:-1] if hint.lower().endswith("s") and not hint.lower().endswith("ss") else hint
                hoist(node["items"], item_hint)
            if isinstance(node.get("additionalProperties"), dict):
                hoist(node["additionalProperties"], hint)
            if "oneOf" in node:
                for sub in node["oneOf"]:
                    hoist(sub, hint)
            if "properties" in node:
                for key, sub in node["properties"].items():
                    hoist(sub, key)
            if node.get("type") == "object" and "properties" in node:
                description = node.pop("description", None)
                key = json.dumps(node, sort_keys=True)
                name = existing_by_shape.get(key)
                if name is None:
                    name = unique_name(hint)
                    schemas[name] = dict(node)
                    existing_by_shape[key] = name
                node.clear()
                node["$ref"] = f"#/components/schemas/{name}"
                if description:
                    node["description"] = description
        elif isinstance(node, list):
            for item in node:
                hoist(item, hint)

    # Hoist inline objects nested inside already-named schemas, without
    # renaming/hoisting the named schema itself.
    for name, schema in list(schemas.items()):
        for key, sub in schema.get("properties", {}).items():
            hoist(sub, f"{name} {key}")
        if isinstance(schema.get("additionalProperties"), dict):
            hoist(schema["additionalProperties"], name)
        if "items" in schema:
            hoist(schema["items"], name)

    # Hoist inline objects used directly in endpoint request/response bodies.
    for path_item in openapi["paths"].values():
        for op in path_item.values():
            op_id = op.get("operationId", "Body")
            content = op.get("responses", {}).get(
                "200", {}).get("content", {}).get("application/json")
            if content and "schema" in content:
                hoist(content["schema"], f"{op_id} Response")
            request_content = op.get("requestBody", {}).get(
                "content", {}).get("application/json")
            if request_content and "schema" in request_content:
                hoist(request_content["schema"], f"{op_id} Request")


def unify_realm_fields(openapi):
    """
    Point every object property literally named "realm" at the shared
    Realm enum schema instead of each object's own inline (and often
    narrower) enum, so there's a single definition of what a realm is.
    """
    for name, schema in openapi["components"]["schemas"].items():
        if name == "Realm":
            continue
        prop = schema.get("properties", {}).get("realm")
        if prop and prop.get("type") == "string":
            new_prop = {"$ref": "#/components/schemas/Realm"}
            if prop.get("description"):
                new_prop["description"] = prop["description"]
            schema["properties"]["realm"] = new_prop


poe1_version, poe2_version = fetch_latest_versions()
realm_info = {
    "pc": {"title": "Path of Exile API", "version": poe1_version},
    "xbox": {"title": "Path of Exile API (Xbox)", "version": poe1_version},
    "sony": {"title": "Path of Exile API (Sony)", "version": poe1_version},
    "poe2": {"title": "Path of Exile 2 API", "version": poe2_version},
}

soup = fetch_soup("https://www.pathofexile.com/developer/docs/reference")

for realm in realms:
    openapi = build_openapi(soup, realm)
    apply_go_type_overrides(openapi)
    hoist_object_schemas(openapi)
    unify_realm_fields(openapi)
    suffix = "-poe1" if realm == "pc" else f"-{realm}"

    with open(f"out/openapi{suffix}.json", "w", encoding="utf-8") as f:
        json.dump(openapi, f, indent=2, ensure_ascii=False)

    with open(f"out/openapi{suffix}.yaml", "w", encoding="utf-8") as f:
        yaml.dump(openapi, f, allow_unicode=True, sort_keys=False)
