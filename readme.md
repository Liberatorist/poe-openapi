# OpenAPI Spec for GGG's PoE API

This product isn't affiliated with or endorsed by Grinding Gear Games in any way

## How to generate the spec

```bash
pip install -r requirements.txt
python3 generate.py
```

This generates one spec per realm, with the realm baked into the paths and query parameters:

- `openapi.json` / `openapi.yaml` — PoE1 PC (the default realm)
- `openapi-xbox.json` / `openapi-xbox.yaml` — PoE1 Xbox
- `openapi-sony.json` / `openapi-sony.yaml` — PoE1 Sony
- `openapi-poe2.json` / `openapi-poe2.yaml` — PoE2

Endpoints that a realm doesn't support (per GGG's docs) are omitted from that realm's spec.

## Disclaimer

This might break the moment GGG changes anything in the way they want to display their spec, so be warned.
