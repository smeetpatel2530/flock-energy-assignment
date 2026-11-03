# Urja Meter Ops API

A small, read-only REST API in front of **Urja Meter Ops**, a legacy internal web portal that
electricity-distribution staff use to look up smart meters. The portal has no public API; this
service gives other engineers and programs clean JSON access to its data without touching the
portal themselves.

- **How the portal works under the hood:** [PROTOCOL.md](PROTOCOL.md)
- **API specification (OpenAPI 3.1):** [openapi.json](openapi.json), also rendered at
  `http://localhost:8000/docs` while the service runs
- **Reflection:** [REFLECTION.md](REFLECTION.md)

---

## The problem

Urja Meter Ops (`https://urja-ops.flockenergy.tech`) shows, for each of ~400 smart meters:
nameplate details, where the meter sits in the electricity network (zone → … → distribution
transformer), its location, and its recent energy readings. It is built for humans clicking
around. Teams wanting the data programmatically have been copy-pasting from the browser.

## What I built

A FastAPI service that logs in to the portal as a normal user, pulls data through the same
endpoints the portal's own pages use, and re-exposes it through a deliberately small API:

| Method & path | What it returns |
|---|---|
| `GET /meters` | Meters, filterable by status, type, phase, make, DT, feeder or ID/serial substring; paginated |
| `GET /meters/{meter_id}` | One meter: nameplate, location, ordered network position |
| `GET /meters/{meter_id}/consumption` | Latest available readings with per-interval kWh consumed |
| `GET /network` | Network hierarchy (zone → DT) with meter counts, plus a data-quality report |
| `GET /transformers` | Distribution transformers with feeder and capacity |
| `GET /health` | Liveness (does not contact the portal) |

Short version of the investigation (details in [PROTOCOL.md](PROTOCOL.md)): the portal is a
SvelteKit app whose pages fetch JSON from private `/portal/*` endpoints, authenticated by a
session cookie from a form login. Its Transformers page has an **Export** button that calls a
**signed bulk endpoint** (`HMAC-SHA256` with a secret the portal hands to logged-in users),
returning every meter with hierarchy and coordinates in one response. Energy readings are only
available per meter. The portal rate-limits bursts.

## Architecture

```
client ──HTTP──▶ app/main.py        routes, validation, error → status-code mapping
                    │
                    ▼
                 app/service.py     cached meter snapshot, filtering, pagination
                 app/network.py     hierarchy tree + data-quality report from meter paths
                    │
                    ▼
                 app/portal/        everything portal-specific
                   client.py          login, session refresh, throttle, 429 backoff, export signing
                   parsing.py         raw portal JSON → public models (quirk handling lives here)
                   errors.py          portal failure types
                    │
                    ▼
              Urja Meter Ops portal
```

`app/models.py` holds the public response models. They are our contract and intentionally do
not mirror portal payloads (no `build` field, no string-typed numbers, no `DD/MM/YYYY` dates).

```
app/            service code
tests/          pytest suite (no network access needed)
scripts/        export_openapi.py: regenerates openapi.json from the app
PROTOCOL.md     portal investigation write-up
openapi.json    generated API spec
```

## Setup

Requires Python 3.11+.

```bash
git clone <this repo>
cd <repo>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Configuration

Copy the template and fill in the portal login you were given:

```bash
cp .env.example .env
```

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `URJA_PORTAL_URL` | no | `https://urja-ops.flockenergy.tech` | Portal base URL |
| `URJA_USERNAME` | **yes** | | Portal login email |
| `URJA_PASSWORD` | **yes** | | Portal password |
| `URJA_TIMEOUT_SECONDS` | no | `15` | Per-request timeout to the portal |
| `URJA_MIN_REQUEST_INTERVAL_SECONDS` | no | `0.5` | Minimum gap between portal requests |
| `URJA_MAX_RATE_LIMIT_RETRIES` | no | `3` | Retries on portal HTTP 429 (2s, 4s, 8s backoff) |
| `URJA_CACHE_TTL_SECONDS` | no | `600` | How long the meter snapshot is reused |

`.env` is read from the directory you start the service in, so run commands from the repository
root. If `URJA_USERNAME` or `URJA_PASSWORD` is missing, startup fails with a "Field required"
error naming the missing setting. `.env` is git-ignored. Credentials are only used to talk to the portal and are never returned
by the API or written to logs.

## Running

```bash
uvicorn app.main:app --port 8000
```

The first meter request takes a couple of seconds (login + bulk export, about 1.6 s when tested);
later ones are served from memory. All endpoints and examples below were run against the live
portal.

## Example requests

```bash
curl http://localhost:8000/meters/J100001
```

```bash
curl "http://localhost:8000/meters?install_status=faulty&phase_type=three&page_size=5"
```

```bash
curl http://localhost:8000/meters/J100001/consumption
```

### Example response: `GET /meters/J100001`

Values are taken from the live portal record for this meter (it is test data, not sensitive).

```json
{
  "meter_id": "J100001",
  "serial_number": "GE84132",
  "make": "L&T",
  "phase_type": "single",
  "install_status": "installed",
  "install_type": "ct_operated",
  "dt_code": "DT-002",
  "location": { "latitude": 26.822136543835608, "longitude": 75.90718190602279 },
  "network": [
    { "level": "zone", "code": "Z-02", "name": "Jaipur Zone 2" },
    { "level": "circle", "code": "C-02", "name": "Circle 2" },
    { "level": "division", "code": "D-02", "name": "Division 2" },
    { "level": "subdivision", "code": "SD-02", "name": "Subdivision 2" },
    { "level": "substation", "code": "SS-02", "name": "Substation 2" },
    { "level": "feeder", "code": "F-002", "name": "Feeder 2" },
    { "level": "dt", "code": "DT-002", "name": "Mansarovar DT 2" }
  ]
}
```

### Example response: `GET /meters/J100001/consumption` (readings truncated)

```json
{
  "meter_id": "J100001",
  "interval_minutes": 30,
  "period_start": "2026-06-23T23:30:00",
  "period_end": "2026-06-30T23:30:00",
  "total_kwh_consumed": 138.8,
  "skipped_rows": 0,
  "readings": [
    { "timestamp": "2026-06-23T23:30:00", "kwh_register": 42594.05, "kvah_register": 46001.58, "voltage_r": 231.0, "kwh_consumed": null },
    { "timestamp": "2026-06-24T00:00:00", "kwh_register": 42594.47, "kvah_register": 46002.02, "voltage_r": 230.0, "kwh_consumed": 0.42 }
  ]
}
```

### Example response: `GET /network` (tree truncated)

Data-quality numbers below are the live portal's actual values.

```json
{
  "zones": [
    {
      "level": "zone", "code": "Z-01", "name": "Jaipur Zone 1", "meter_count": 143,
      "children": [
        { "level": "circle", "code": null, "name": "Circle 1", "meter_count": 1, "children": ["..."] },
        { "level": "circle", "code": "C-01", "name": "Circle 1", "meter_count": 72, "children": ["..."] },
        { "level": "circle", "code": "C-04", "name": "Circle 4", "meter_count": 70, "children": ["..."] }
      ]
    }
  ],
  "data_quality": {
    "codes_under_many_parents": [
      { "level": "division", "code": "D-01", "parent_codes": [null, "C-01", "C-03", "C-05"] }
    ],
    "codes_with_many_names": [
      { "level": "dt", "code": "DT-007", "names": ["Old Malviya Nagar Xfmr", "Sanganer DT 7"] }
    ],
    "missing_values": [
      { "level": "circle", "meters_missing_code": 6, "meters_missing_name": 5 },
      { "level": "substation", "meters_missing_code": 3, "meters_missing_name": 0 },
      { "level": "feeder", "meters_missing_code": 5, "meters_missing_name": 3 }
    ]
  }
}
```

### Errors

Errors share one shape: `{"error": "<code>", "message": "<text>"}`.

| Status | `error` | When |
|---|---|---|
| 404 | `meter_not_found` | Unknown meter ID (IDs are case-sensitive, as in the portal) |
| 422 | (FastAPI validation body) | Invalid query parameters, e.g. `page=0` |
| 502 | `portal_auth_failed` | Portal rejected the configured credentials |
| 502 | `portal_bad_response` | Portal answered with something unparseable |
| 503 | `portal_unavailable` | Portal unreachable, 5xx, or still rate-limiting after retries |
| 504 | `portal_timeout` | Portal did not answer in time |

## Tests

```bash
pytest
```

48 tests, all offline (the portal is faked with `httpx.MockTransport` or a stub client):

- **parsing:** export record → `Meter`, blank hierarchy values → `null`, readings sorted,
  deltas computed, duplicate/blank rows dropped, empty data.
- **client:** login form format, bad credentials, session expiry → one re-login, 429 backoff
  and give-up, 404, unsafe meter IDs never reaching the portal, timeout, export signing (checked against a signature produced by the
  portal's own browser code), secret refetch, DT pagination.
- **API:** filters, pagination, validation (422), 404s, portal failures → 502/503/504,
  snapshot caching, serving stale data when a refresh fails.
- **network:** meter counts per node, a reused code becoming separate nodes and being reported,
  missing names not splitting nodes, missing codes, one code with two names.
- **openapi.json** is checked against the running app so it cannot drift.

Regenerate the spec after changing routes or models:

```bash
python -m scripts.export_openapi
```

---

## Assumptions

- **Readings are cumulative registers.** `kwh`/`kvah` only ever increase, so I treat them as
  meter registers and compute consumption as the difference between consecutive readings.
  Units (kWh, kVAh, V) come from field names and the portal's column headers.
- **Timestamps are local time.** The portal sends `DD/MM/YYYY HH:mm` with no zone. The utility
  is in Jaipur, so Asia/Kolkata is likely, but I return the naive local time rather than
  inventing an offset.
- **The bulk export is authoritative for meter metadata.** Sampled export records matched the
  per-meter detail page and `/geo` endpoint exactly.
- **"Recent consumption" means the latest data the portal has.** The portal's window is fixed at
  23–30 June 2026; there is no date-range parameter.
- **Using the export is legitimate.** It is a button in the portal's own UI, available to a
  normal user. The client signs requests exactly as the portal's JavaScript does.
- **Hierarchy data is reported as the portal gives it.** Codes reused under different parents,
  blank codes and renamed nodes are passed through (blanks as `null`), not "fixed". Within one
  parent path, records with the same code are treated as the same node even if some lack its name.

## Design decisions & trade-offs

**Meter metadata from the bulk export, cached in memory.**
*Why:* one signed call returns all 403 meters with hierarchy and coordinates. The alternative,
search pages plus one detail request per meter, is 400+ requests against a portal where
~140 requests in ~2 minutes was enough to trigger HTTP 429s. It also has to parse two different
detail payload formats (`legacy` label/value lists vs `v2` stringified JSON) out of SvelteKit's
internal `__data.json` encoding.
*Cost:* data can be up to `URJA_CACHE_TTL_SECONDS` (10 min) stale, and a meter added to the
portal is not visible until the next refresh. If a refresh fails, the previous snapshot is
served, so the API degrades rather than errors. Filtering 403 records in memory is trivial;
this would need revisiting at roughly 10⁵+ meters or with multiple service instances (then a
shared store such as SQLite/Postgres refreshed by a background job).

**Consumption fetched live, not cached.** Per-meter, not in the export, one request each.
Caching would add invalidation questions for no demonstrated need.

**No database.** Nothing must survive a restart: the snapshot is rebuilt with one export call.

**Sync `httpx` client behind a lock, one portal request at a time, ≥0.5 s apart.** The portal
rate-limits, so concurrency would only trigger 429s sooner. A sync client is simpler, and FastAPI
runs sync endpoints in a thread pool.

**Retry policy kept narrow:** one re-login on 401 (portal sessions last one hour), exponential backoff on 429
(there is no `Retry-After`), one secret refetch on a rejected export signature. No retries on
timeouts or 5xx, to avoid amplifying load on a struggling portal.

**Portal failures are gateway errors (502/503/504), not 500.** They tell the caller the problem
is upstream and whether retrying later makes sense.

**snake_case, normalised enums (`ct_operated`, `faulty`), real numbers and ISO timestamps** in
the public API; the portal's casing and string-typed numbers stay behind `parsing.py`. Enum-like
fields are plain strings (with observed values documented) so an unexpected portal value does
not break responses.

**FastAPI** mainly because it generates `openapi.json` from the code (no hand-written spec to
drift) and gives Swagger UI for free.

## What I intentionally left out

- **"Repairing" the hierarchy.** Codes are reused under different parents, and I could not
  establish whether codes are unique only within their parent or the links are wrong
  (PROTOCOL.md §9). `GET /network` therefore keys each node by its full path, which is correct
  under either reading, and reports the inconsistencies instead of choosing a "true" parent.
  The cost: 40 real DTs show up as 54 DT nodes.
- **Parsing the per-meter detail page** (`__data.json`, `legacy`/`v2` formats). The export
  covers the same fields more reliably. Documented in PROTOCOL.md in case the export disappears.
- **Search pass-through to the portal.** Filtering the cached snapshot is strictly more capable.
- **Date-range filtering on consumption.** The portal offers no range, and the window is only
  7 days.
- **Geo queries ("near this point"), a web client, persistence.** Optional; core first.
- **Auth on our own API.** It runs locally; production would need it.

## What I would improve with more time

1. Run the service for more than an hour to confirm a natural session expiry (the cookie has
   `Max-Age=3600`; recovery was only verified by deleting the cookie) and whether the session
   renews on activity.
2. Measure the actual rate limit (gently) and tune throttling from data rather than a guess.
3. Backfill the 5 blank feeder codes from `/portal/dts`. That source agrees with the meter
   paths for all 398 meters where both have a value, so the repair is backed by evidence. Then ask
   the portal's owners how network codes are meant to be scoped, the only real way to resolve the
   ambiguity above the feeder.
4. Verify the half-hourly vs daily split across all 403 meters, not a sample.
5. Background refresh of the snapshot so no request ever pays the export latency.
6. Structured logging and metrics (portal latency, 429 count, snapshot age), plus exposing
   snapshot age in responses.
7. A `Dockerfile` for one-command startup.
