import json
import yaml
from bs4 import BeautifulSoup
from itertools import product
from urllib.request import Request, urlopen
import requests


primitive_translations = {
    "string": {"type": "string"},
    "uint": {"type": "integer", "format": "uint32"},
    "double":  {"type": "number", "format": "double"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "int": {"type": "integer", "format": "int32"},
    "Error": {"type": "object", "properties": {"code":  {"type": "integer", "enum": [200, 202, 400, 404, 429, 500]}, "message": {"type": "string"}}},
}

http_verbs = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}


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


def parse_html_to_openapi(url):
    response = requests.get(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    })
    soup = BeautifulSoup(response.text, "html.parser")
    openapi = {
            "openapi": "3.1.0",
            "info": {"title": "Path of Exile API", "version": "3.27.0"},
            "paths": {},
            "servers": [
                {
                    "url": "https://api.pathofexile.com"
                }
            ],
            "components": {
                "schemas": {},
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
            "parameters": [
                {
                    "name": "User-Agent",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string"
                    },
                    "description": "format: OAuth {$clientId}/{$version} (contact: {$contact})"
                }
            ],
            "tags": []
        }
    h2s = soup.find_all("h2")
    schemas = openapi["components"]["schemas"]
    tags = openapi["tags"] = []
    for h2 in h2s:
        tag = ""
        scope = ""
        scopedivs = find_all_before(h2, "div", "h2")
        if scopedivs:
            if "scope" in scopedivs[0].text.lower():
                x = scopedivs[0].find_next("a")
                if x:
                    scope = x.text.strip()
                tag = h2.text.strip()
                tags.append({
                    "name": h2.text.strip(),
                })
        for h3 in find_all_before(h2, "h3", "h2"):
            text = h3.text.strip()
            if text.startswith("object"):
                table = h3.find_next("table")
                if table:
                    schemas[text.split(" ")[1]] = parse_table(table)
            else:
                parse_endpoint(openapi, tag, scope, h3)

    return openapi


def parse_endpoint(openapi, tag, scope, h3):
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
    for part in path.split("/"):
        part = part.strip().replace("[", "")
        if part.startswith("<") and part.endswith(">"):
            pname = part[1:-1]
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


openapi = parse_html_to_openapi(
    "https://www.pathofexile.com/developer/docs/reference")

with open("openapi.json", "w", encoding="utf-8") as f:
    json.dump(openapi, f, indent=2, ensure_ascii=False)

with open("openapi.yaml", "w", encoding="utf-8") as f:
    yaml.dump(openapi, f, allow_unicode=True, sort_keys=False)
