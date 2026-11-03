# Urja Meter Ops — Portal Protocol

How the legacy portal at `https://urja-ops.flockenergy.tech` actually works, as discovered by
watching its network traffic and reading its client-side JavaScript while logged in as a normal
operator.

Every statement is tagged:

- **[Observed]** — seen directly in a real request/response.
- **[Inferred]** — a conclusion drawn from observations, not proven.
- **[Unknown]** — not yet established; listed under *Open questions*.

Sanitised examples only. No credentials, cookies or the export signing secret appear here.

---

## 1. Overview

- **[Observed]** The portal is a **SvelteKit** app (`/_app/immutable/...` bundles, `__data.json`
  routes, `x-sveltekit-action` form handling).
- **[Observed]** Despite having "no API", the browser fetches almost everything as **JSON** from a
  private set of endpoints under `/portal/*`. HTML pages are thin shells around those calls.
- **[Observed]** Pages (from the route manifest in `entry/app.*.js`):

  | Page | Data source |
  |---|---|
  | `/login` | SvelteKit form action `POST /login` |
  | `/meters` | `GET /portal/meters/search` |
  | `/meters/[id]` | server load (`/meters/[id]/__data.json`) + `GET /portal/meters/[id]/geo` + `GET /portal/meters/[id]/energy` |
  | `/transformers` | `GET /portal/dts`, plus an "Export" button using `/portal/keys` + `/portal/export` |

- **[Observed]** Dataset size: **403 meters**, **40 distribution transformers (DTs)**.

### How this was discovered

1. Opened the logged-in portal and listed network requests → saw `/portal/meters/search`.
2. Only some JS bundles load per page, so I read the route manifest in `entry/app.*.js`, fetched
   every `nodes/*.js` page bundle and searched them for `/portal`, `/api`, `/export` string literals.
   That revealed `/portal/dts`, `/portal/keys`, `/portal/export`, `/geo` and `/energy` before I had
   visited those pages.
3. Visited each page and replayed each endpoint to confirm request/response shapes.

---

## 2. Authentication

### Login

- **[Observed]** Login form (`/login`) has fields `email` (type email) and `password`, `method="POST"`.
- **[Observed]** The browser submits it as a SvelteKit form action:

  ```
  POST /login
  Content-Type: application/x-www-form-urlencoded
  Accept: application/json
  x-sveltekit-action: true

  email=...&password=...
  ```

- **[Observed]** Responses (HTTP 200 in both cases; the real outcome is inside the JSON):

  ```json
  {"type":"redirect","status":303,"location":"/meters"}
  ```
  ```json
  {"type":"failure","status":401,"data":"[{\"email\":1,\"error\":2},\"operator@urja.local\",\"Invalid email or password.\"]"}
  ```

  The `data` of a failure is SvelteKit's *devalue* encoding (a flat array where objects reference
  other entries by index), not plain JSON.

- **[Observed]** An **`Origin` header matching the portal is mandatory.** Without it (as from a
  plain HTTP client) the portal answers
  `403 {"message":"Cross-site POST form submissions are forbidden"}`, even with correct
  credentials. This is SvelteKit's built-in CSRF protection; browsers send `Origin`
  automatically, which is why it never showed up in browser traffic.

### Session

- **[Observed]** After login, `document.cookie` is empty and nothing is stored in
  `localStorage`/`sessionStorage`. The session is an HttpOnly cookie that JS cannot read.
- **[Observed]** (from the `Set-Cookie` header when logging in from Python) the cookie is:
  ```
  __Secure-better-auth.session_token=<opaque>; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax
  ```
  The name shows the portal uses the **Better Auth** library. Sessions last **1 hour**.
- **[Observed]** Every `/portal/*` call is authenticated purely by that cookie; no
  `Authorization` header or CSRF token is sent.
- **[Observed]** Without the cookie:
  - `/portal/*` → `401 {"error":"unauthorized","message":"A valid session is required."}`
  - HTML pages (e.g. `/meters`) → redirect (to `/login`, **[Inferred]**).
- **[Observed]** Sign-out button: `POST /api/auth/sign-out`, then the browser goes to `/login`.

### Open questions (auth)

- **[Unknown]** Whether the 1-hour session is sliding (renewed on activity) or fixed. Not
  observed over a full hour.
- **[Observed]** A missing session cookie gives `401 unauthorized`, and logging in again
  recovers (verified live by clearing the cookie mid-session). **[Inferred]** An expired cookie
  behaves the same way.
- **[Unknown]** The first login attempt during exploration returned the 401 failure and the
  second succeeded. Not yet established whether that was a typo or portal behaviour.

---

## 3. Meter list / search

```
GET /portal/meters/search?q=<text>&page=<n>
```

- **[Observed]** Response:

  ```json
  {
    "data": [
      {"meterId":"J100000","serialNo":"SE33962","make":"HPL","phaseType":"single",
       "installStatus":"Decommissioned","dtCode":"DT-001"}
    ],
    "total": 403, "page": 1, "pageSize": 20
  }
  ```

- **[Observed]** `pageSize` is fixed at **20**; a `pageSize` query param is ignored.
- **[Observed]** `q` matches **substrings of `meterId` and `serialNo`** (`q=J10001` → J100010…;
  `q=SE33962` → 1 hit). It does **not** match `dtCode` (`DT-001` → 0) or `make` (`Genus` → 0).
- **[Observed]** `page=99` → `200` with `data: []`, `total: 403`. `page=0` returns the first page.
- **[Observed]** List rows lack `installType`, hierarchy and geo — the detail endpoints or the
  export are needed for those.

---

## 4. Meter detail (nameplate + hierarchy)

The detail page is **server-rendered**; its data is available as SvelteKit page data:

```
GET /meters/<meterId>/__data.json
```

- **[Observed]** Response is SvelteKit/devalue-encoded. Node 0 = layout, node 1 = current user
  (`{"name":"Ops Desk","email":"..."}`), node 2 = the meter.
- **[Observed]** Meter IDs are **case-sensitive**: `j100001` → not found.
- **[Observed]** Unknown meter → HTTP **200** with the error *inside* the payload:
  `{"type":"error","error":{"message":"Meter not found"},"status":404}`.

### Two different detail shapes (`build`)

- **[Observed]** Meters come in two "builds" (the export exposes a `build` field: 238 `legacy`,
  165 `v2`). The **detail payload has a different shape per build**:

  **legacy** — list of label/value pairs:
  ```json
  {"data":[{"parameterName":"Meter ID","parameterValue":"J100000"},
           {"parameterName":"Serial No","parameterValue":"SE33962"},
           {"parameterName":"Make","parameterValue":"HPL"},
           {"parameterName":"Phase Type","parameterValue":"single"},
           {"parameterName":"Installation Status","parameterValue":"Decommissioned"},
           {"parameterName":"Installation Type","parameterValue":"Whole Current"}]}
  ```

  **v2** — a *JSON string* inside JSON, PascalCase keys:
  ```json
  {"classData":"{\"installed_meter\":{\"MeterId\":\"J100004\",\"SerialNo\":\"SE65293\",\"Make\":\"Genus\",\"PhaseType\":\"single\",\"InstallationStatus\":\"Faulty\",\"InstallationType\":\"CT Operated\"}}"}
  ```

- **[Observed]** The portal's own page code handles both: if `classData` is present it
  `JSON.parse`s it and reads `installed_meter`, otherwise it maps `data[]`.

- **[Observed]** Hierarchy on the detail page is a flat map of display strings:
  ```json
  {"Zone":"Jaipur Zone 2 (Z-02)","Circle":"Circle 5 (C-05)","Division":"Division 5 (D-05)",
   "Subdivision":"Subdivision 5 (SD-05)","Sub Station":"Substation 5 (SS-05)",
   "Feeder":"Feeder 5 (F-005)","DT":"Bani Park DT 5 (DT-005)"}
  ```
  Format is `"<name> (<code>)"`; when the code is missing it is just `"<name>"`
  (e.g. `"Circle 6"`). The page drops empty levels from the breadcrumb.

---

## 5. Location

```
GET /portal/meters/<meterId>/geo
```

- **[Observed]** `{"data":{"latitude":"26.822136543835608","longitude":"75.90718190602279"}}`
  — coordinates are **strings** here (the export has them as numbers).
- **[Observed]** Unknown meter → `404 {"error":"not_found","message":"Meter not found"}`.
- **[Observed]** Sampled values match the export's `geo.lat/lng` for the same meters.

---

## 6. Consumption (energy readings)

```
GET /portal/meters/<meterId>/energy
```

- **[Observed]** No query parameters are sent by the UI; no date-range parameter is known.
- **[Observed]** Response:
  ```json
  {"data":[{"timestamp":"23/06/2026 23:30","kwh":"42594.05","kvah":"46001.58","voltR":"231"}, ...]}
  ```
  - all values are **strings**;
  - `timestamp` is `DD/MM/YYYY HH:mm`, **no timezone**;
  - `kwh` / `kvah` **only increase** across rows.
- **[Inferred]** `kwh`/`kvah` are **cumulative register readings** (meter odometer), not
  per-interval consumption. Interval consumption = difference between consecutive readings.
  `voltR` is likely R-phase voltage in volts. Units are inferred from field names and the
  portal's column headers (`kWh`, `kVAh`, `V(R)`), not documented anywhere.
- **[Observed]** Two granularities:
  - **337 half-hourly rows** (23/06/2026 23:30 → 30/06/2026 23:30) for meters J100000–J100039 in
    my sample (7 meters checked).
  - **8 daily rows** at `00:00` (23/06 → 30/06/2026) for meters J100040 and above in my sample
    (~35 meters checked).
  - **[Inferred]** The first 40 meters (one per DT?) have interval data and the rest daily. Not
    verified across all 403 meters, and none of `build`/`phaseType`/`installType`/`installStatus`
    explains the split.
- **[Observed]** The data window is fixed at **23–30 June 2026**, i.e. months before "today"
  (Nov 2026). "Recent" means "latest available", not "last 7 days from now".
- **[Observed]** Malformed rows exist. J100195 ends with a **duplicate timestamp** row with empty
  `kwh` and `voltR` but a repeated `kvah`:
  ```json
  {"timestamp":"30/06/2026 00:00","kwh":"24530.81","kvah":"26493.27","voltR":"240"},
  {"timestamp":"30/06/2026 00:00","kwh":"","kvah":"26493.27","voltR":""}
  ```
- **[Observed]** Unknown meter → `404 {"error":"not_found","message":"Meter not found"}`.

---

## 7. Distribution transformers

```
GET /portal/dts?page=<n>
```

- **[Observed]** `{"data":[{"code":"DT-001","name":"Malviya Nagar DT 1","feederCode":"F-001","capacityKva":100}, ...],"total":40,"page":1,"pageSize":20}`
- **[Observed]** `capacityKva` is a **number** (unlike most other numeric fields).

---

## 8. Bulk export (signed)

The Transformers page has an **Export** button that downloads `meter-export.json`. It is the only
place where all meters, hierarchy and coordinates come back in one call.

### Flow (from `nodes/4.*.js`, then replayed)

1. `GET /portal/keys` → `{"data":{"signingSecret":"<32-char string>"}}` (session-authenticated).
2. Build a signature:
   ```
   timestamp = floor(now_unix_seconds)
   message   = METHOD + "\n" + PATH + "\n" + QUERY + "\n" + timestamp
             = "GET\n/portal/export\npage=1\n<unix seconds>"
   signature = lowercase_hex(HMAC_SHA256(key = signingSecret, msg = message))
   ```
3. `GET /portal/export?page=1` with headers `x-timestamp: <timestamp>` and
   `x-signature: <signature>`.

### Response

- **[Observed]** `{"data":[...403 meters...],"total":403}`, each record:
  ```json
  {
    "meterId":"J100000","serialNo":"SE33962","make":"HPL","phaseType":"single",
    "installStatus":"Decommissioned","installType":"Whole Current","build":"legacy",
    "dtCode":"DT-001",
    "geo":{"lat":26.9389,"lng":75.8309},
    "hierarchy":{
      "zone":{"code":"Z-01","name":"Jaipur Zone 1"},
      "circle":{"code":"C-01","name":"Circle 1"},
      "division":{"code":"D-01","name":"Division 1"},
      "subdivision":{"code":"SD-01","name":"Subdivision 1"},
      "substation":{"code":"SS-01","name":"Substation 1"},
      "feeder":{"code":"F-001","name":"Feeder 1"},
      "dt":{"code":"DT-001","name":"Malviya Nagar DT 1"}
    }
  }
  ```
  No energy data in the export.

### Behaviour

- **[Observed]** `page` is **ignored**: `page=1`, `page=2` and no query all return the full 403
  (the signature must still match whatever query string is sent).
- **[Observed]** Missing signature, wrong secret, or a timestamp 10 minutes old →
  `401 {"error":"signature_invalid","message":"Missing or invalid request signature."}`.
- **[Observed]** The signing secret was identical across two consecutive `/portal/keys` calls.
- **[Unknown]** Exact timestamp tolerance window; whether the secret rotates per session/over time.
  The client should fetch the secret fresh rather than cache it long-term.
- **[Observed]** Sampled export records match the detail page and `/geo` for the same meter.

---

## 9. Network hierarchy

7 levels: **Zone → Circle → Division → Subdivision → Substation → Feeder → DT → Meter**.

Distinct codes in the export: zone 3, circle 7 (incl. blank), division 10, subdivision 14,
substation 19, feeder 29, DT 40.

### Data quality — observed in the export

- **Codes are reused under different parents.** For example D-01 appears under C-01, C-03, C-05;
  F-001 under SS-01 and SS-11; SS-01 under SD-01, SD-05, SD-09. Every level below circle shows
  this.
- **Blank codes with names present**: e.g. J100011 has circle `{"code":"","name":"Circle 6"}`.
- **Blank names with codes present**: e.g. circle `C-01` sometimes named `""`, feeder `F-003`
  sometimes `""`.
- **One code, two names**: DT-007 is both `"Sanganer DT 7"` and `"Old Malviya Nagar Xfmr"`.
- `meterId` and `serialNo` are unique; every `meterId` matches `J\d{6}`; `dtCode` always equals
  `hierarchy.dt.code`.

### Interpretation

- **[Inferred]** Either (a) codes below circle are only unique *within their parent* (so a node's
  identity is its full path), or (b) the hierarchy has genuinely inconsistent parent links. The
  data alone cannot settle this. The regular pattern (D-01 under C-01/C-03/C-05, i.e. every
  second circle) hints at generated data with deliberate inconsistency rather than a clean
  per-parent numbering scheme.
- **[Inferred]** Blank codes/names look like missing data rather than distinct nodes.
- No endpoint returns the hierarchy as a tree; it only exists per meter.

### Cross-check against the transformer list

`/portal/dts` records each DT's feeder independently of the meter records, so it can serve as a
second source:

- **[Observed]** For **398 of 403** meters the feeder in the meter's path equals the
  `feederCode` the DT list gives for that meter's DT. The other **5** (DT-005, DT-011, DT-015,
  DT-029, DT-039) have a **blank** feeder code in the path; none contradicts the DT list.
  → The **DT → feeder** link is consistent, and blank feeder codes could be backfilled from the
  DT list.
- **[Observed]** Above the feeder, **16 of 29 feeders** appear under more than one upstream chain
  (substation → … → circle). That is where the ambiguity lives, and there is no second source
  to check it against.
- **[Observed]** Building a tree keyed by full path gives 3 zones, 10 circle nodes, 36 division,
  46 subdivision, 49 substation, 54 feeder and 54 DT nodes, compared with 40 actual DTs.

---

## 10. Rate limiting & reliability

- **[Observed]** Bursts of ~140 requests in ~2 minutes trigger
  `429 {"error":"rate_limited","message":"Rate limit exceeded; slow down."}` — with **no
  `Retry-After` header**. Access recovered after ~60–70 s of silence.
- **[Observed]** ~1 request per 1.5 s ran without 429s.
- **[Observed]** Latency was 90–200 ms when idle, rising to 400–800 ms under repeated calls
  before the 429s started. On first page load `/geo` and `/energy` were noticeably slow.
- **[Unknown]** Exact limit (requests per window), whether it is per session or per IP.

---

## 11. Quirks and surprises (summary)

1. A "no API" portal that is internally a JSON API under `/portal/*`.
1. Login from a non-browser client fails with 403 unless an `Origin` header is sent (CSRF check).
2. A hidden bulk export behind an HMAC signature whose secret the portal hands to any logged-in
   user via `/portal/keys`.
3. The export's `page` parameter does nothing.
4. Two detail payload formats (`legacy` label/value list vs `v2` stringified JSON), switched by a
   `build` field that only the export reveals.
5. Numbers as strings everywhere except the export's `geo` and `/portal/dts` `capacityKva`.
6. Dates as `DD/MM/YYYY HH:mm` with no timezone.
7. Energy readings are cumulative registers, at two granularities, for a fixed June 2026 window.
8. Duplicate/empty energy rows (J100195).
9. "Not found" is a real 404 on `/portal/*` but a 200-with-embedded-404 on `__data.json`.
10. Hierarchy codes reused under different parents; blank codes and names; renamed DT.
11. Rate limiting without `Retry-After`.

---

## 12. Investigation log (chronological, honest)

- Started from the logged-in `/meters` page; first useful request was `/portal/meters/search`.
- Searched loaded bundles for endpoint strings — only found search and sign-out, because
  SvelteKit lazy-loads page bundles. Fixed by reading the route manifest and fetching *all*
  node bundles.
- Discovered the export flow in the Transformers bundle and re-implemented its signing function
  in the browser console to replay it.
- Profiled the export for data quality (field sets, distinct values, parent-child consistency).
- Compared `legacy` vs `v2` meters on the detail endpoint → found the two payload shapes.
- **Mistake:** fired ~140 energy requests back to back to profile consumption data and hit the
  rate limiter (and my first analysis script crashed on the 429 body). Re-ran at ~1 req/1.5 s.
- Correlated energy granularity with meter attributes → none matched; ID range did.
- First live run of the Python client: login failed. Credentials were confirmed correct; the raw
  response was a 403 CSRF rejection, not the 401 a wrong password gives. Adding `Origin` fixed it,
  and the successful response finally exposed the session cookie's name and 1-hour lifetime.
