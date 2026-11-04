# Reflection

## What assumptions did you make?

The biggest one is that `kwh` and `kvah` are cumulative meter registers rather than per-interval
consumption. Nothing in the portal says so; I inferred it because the values only ever increase
across every meter I checked. The API therefore returns both the raw register and a computed
`kwh_consumed` delta, so a consumer can see what came from the portal and what was derived.
Second, timestamps have no timezone; I assumed local (likely Asia/Kolkata) but return naive
times rather than claiming an offset I can't verify. Third, I treat the bulk export as the
source of truth for meter metadata, which I justified by spot-checking a handful of meters
against the detail page and `/geo`, not every meter.

## Which part was the most difficult, and how did you get unstuck?

Finding all of the portal's data paths. Searching the JavaScript that had loaded only turned up
the meter search endpoint, because SvelteKit loads each page's code lazily. The way through was
the route manifest in the app entry bundle: it lists every page's bundle, so fetching and
searching all of them revealed `/portal/dts`, `/portal/keys` and the signed `/portal/export`
before I had visited those pages. Understanding the export then meant reading the signing
function and reproducing it, first in the browser console, then in Python, checked against a
signature produced by the portal's own code.

## If you had another day, what would you improve?

I would first investigate the inconsistent network hierarchy, particularly why certain codes appear under multiple parents, and verify whether this reflects legitimate per-parent numbering or incorrect source data. I would also test the service over a longer session to understand authentication expiry and measure the portal's actual rate limits instead of relying on conservative assumptions. Beyond that, I would verify the consumption-data granularity across all 403 meters and move the bulk-export refresh into a background task so API requests do not have to wait for the snapshot to refresh.

## What mistake did you make while solving this?

While profiling energy data I fired roughly 140 requests in about two minutes and hit the
portal's rate limiter (HTTP 429, no `Retry-After`). My analysis script also assumed every
response had a `data` array and crashed on the 429 body, so at first it looked like a parsing
bug rather than throttling. Lessons: treat a legacy system as fragile from the first request,
pace exploratory traffic, and make error handling the first thing in any scraping script. It
directly shaped the client: a minimum gap between requests and backoff on 429.

A second one: I built the login from what the browser sent and assumed that was the whole
request. The first live run failed with what my client reported as "credentials rejected". The
credentials were fine. The portal had returned a 403 CSRF error because my client didn't send the
`Origin` header that browsers add automatically. My code lumped "any non-success login response"
into one auth error and hid the real cause. Fix: send `Origin`, and look at raw responses before
trusting my own error messages.

## If you were reviewing your own submission, what would you criticise?

The hierarchy is passed through rather than modelled, so the "where does this meter sit"
question is only half-answered for messy records. Several conclusions rest on samples
(export-vs-detail agreement, the granularity split). The in-memory snapshot is a single-process
design: two instances would each hold their own copy and each hit the portal. Session expiry
handling is tested against a fake portal but has not been observed live. Finally, the export's
signing secret is fetched from the portal on every refresh; that mirrors the portal's own UI,
but if the portal team considered the export privileged this design would depend on something
they might change.
