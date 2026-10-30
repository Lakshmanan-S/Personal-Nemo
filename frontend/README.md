# Personal Nemo


**The AI that actually knows you**


## Fewer files and a clean export


Shared enums/defaults now live in `backend/app/config.py`; the current-user
dependency lives beside its API routes in `backend/app/routes.py`. The two
frontend TypeScript projects are combined into `tsconfig.json`, using Vite's
bundler resolution for both the browser code and Vite configuration. Strict
unused-code checks remain enabled. This removes four existing files without
removing features, sample data or tests. Runtime and test modules that would
become unwieldy remain separate. The Git ignore rules are consolidated at the
repository root and now exclude credentials, SQLite databases and archives.


The clean `personal-nemo-export.zip` provided with this change contains the
application, samples, tests, configuration and this guide in their original
folder structure. Extract it before uploading source to GitHub. It deliberately
does not contain `.env`, personal databases, OAuth tokens, dependency folders,
build output, test caches or earlier ZIPs. Keep your original local folder as
the private copy; do not overwrite its environment or database.


## Free public demo: Vercel + Render


This deployment is a **public, sample-only demo**, not private personal hosting.
All existing local features remain available with `PUBLIC_DEMO=false` (default).
Hosted public mode:


- Automatically loads the existing fictional profile and evidence, without LLM
  calls, into a separate `personal_nemo_public_demo.db`. It ignores the private
  `DATABASE_URL`, so enabling public mode cannot select the local personal DB.
- Keeps Chat, See the Difference, optional Tavily search, nudges and usage views.
  Chat questions/responses are not saved in the public demo DB; provider data
  handling still applies. Usage records contain counts/costs, not prompt text.
- Makes profile/onboarding/privacy administration read-only and blocks live
  connections, OAuth callbacks, personal-data edits/deletion, ingestion and MCP
  token administration at the API, not just in the UI. Local MCP remains intact;
  the free public blueprint does not expose a second MCP service/port.
- Serializes AI requests through stream completion, allows ten AI requests per
  minute across the shared demo, and caps each posted AI request at 16 KB.
  Run one backend worker. These are demo safeguards, not authentication.
- Still uses **your paid Nebius/Tavily credits**. The blueprint sets a $1 logged
  daily Nebius threshold; it is not a guaranteed provider-side spending limit.
  Calls may overshoot the threshold, Tavily is separate, and free-server restarts
  erase local usage records/rate counters. Set provider-side budgets/quotas and
  monitor your accounts. Remove the API keys when the demo ends.


### Cost and persistence


[Render Free](https://render.com/docs/free) supports this disposable sample
demo without a paid disk or database. It sleeps after 15 idle minutes, can take
about a minute to wake, and loses local files on restarts, redeploys and
spin-downs. Samples reload at startup. Do not expect durable usage history.
Free Render Postgres expires after 30 days, so it is not a durable free solution.
No database migration or paid service is required for this sample demo.


[Vercel Hobby](https://vercel.com/docs/plans/hobby) is intended for eligible
personal/non-commercial projects. Commercial/client production use must follow
Vercel's current plan terms. Both providers have usage limits; verify your
account's billing/spend controls and do not enable paid upgrades automatically.
These settings are for free-tier eligibility, not a promise of unlimited hosting.


### Step 1: upload the clean source


1. Extract the clean export ZIP into a new folder.
2. Create a GitHub repository and upload/commit the extracted source. Keep
   `render.yaml` at the repository root, with `backend` and `frontend` beside it.
   Upload the extracted files, not a ZIP as the repository's only file.
3. Do not upload your original `.env`, databases or existing archives. If using
   Git rather than browser upload, the root `.gitignore` protects these files.


### Step 2: deploy the Render API


1. In Render choose **New > Blueprint**, connect the repository and select the
   root `render.yaml`. Confirm the service plan is **Free**. No disk/database
   service is defined.
2. Enter `NEBIUS_API_KEY` and, for web search, `TAVIL_API_KEY` in Render's
   environment UI. Never put them in GitHub or Vercel's frontend variables.
3. Set `FRONTEND_URL` to your exact Vercel production origin if already known.
   Otherwise use `https://example.invalid` temporarily, then replace it in
   Step 4. Browser API calls will not work until the real origin is configured.
4. The blueprint sets `APP_ENV=production` and `PUBLIC_DEMO=true`, installs
   `backend/requirements.txt`, and runs:
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`.
   It pins the Python 3.11 line used by this project.
5. After deployment, copy the HTTPS API origin, e.g.
   `https://personal-nemo-api.onrender.com`. Open its `/health` endpoint and
   wait for `{"status":"ok"}`. Startup seeds samples without paid requests.
6. Do not add Google credentials, an encryption key or your personal database:
   live OAuth is intentionally unavailable on this shared public demo.


### Step 3: deploy the Vercel frontend


1. Import the same GitHub repository into Vercel.
2. Set **Root Directory** to `frontend` and framework to **Vite**. The included
   `frontend/vercel.json` sets `npm ci`, `npm run build`, and output `dist`.
   Use a Node version supported by Vite 8 (22.12+; Node 22.x/24.x are suitable).
3. Add these **Production** environment variables before building:
   - `VITE_API_BASE_URL=https://YOUR-API.onrender.com`
   - `VITE_PUBLIC_DEMO=true`
4. The API value is the backend **origin only**, without `/api`, query strings
   or credentials. Public builds fail explicitly for missing/non-HTTPS origins.
5. Deploy and copy the actual Vercel production origin. Vite variables are
   public and baked into the build: changing them requires a redeploy.


### Step 4: finish CORS and verify


1. In Render set `FRONTEND_URL` to the exact Vercel production origin, e.g.
   `https://personal-nemo.vercel.app`, without a path. Save and redeploy/restart.
2. Open Vercel and verify the **Public sample demo** notice and loaded sample
   profile. Test Chat and See the Difference with a small question; these
   requests consume your API credits. Optional web search uses Tavily credits.
3. Verify Connect/edit/onboarding administration is disabled and the backend
   rejects direct private-data mutations with 403.
4. CORS allows the configured origin, not arbitrary Vercel preview URLs.
   Use the production URL; configure/redeploy deliberately for another origin.
5. If the API appears offline, wait for Render's cold start and retry. A CORS
   error means check the exact frontend origin and restart after changing it.
6. After the demo remove `NEBIUS_API_KEY` and `TAVIL_API_KEY` from Render and
   redeploy. Paid AI then reports an explicit disabled/unconfigured error.


Production startup refuses `PUBLIC_DEMO=false`: a real personal cloud deployment
needs authentication, account isolation and persistent storage first. Do not
try to expose your local private mode by bypassing this check.


## Local demo


Start the backend on port 8000, then run `npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`.
Open http://localhost:5173, load sample data on Home, and use **See the Difference**
to compare generic and profile-aware answers. Loading sample data makes no LLM
calls; generating comparisons and chat answers uses paid Nemotron requests.
Home nudges are generated only after clicking **Generate a nudge**, never on page load.
Validate frontend changes with `npm run build`.
Chat and comparison responses share Markdown rendering for headings, lists,
emphasis, links, code, and tables. Tables scroll within their cards on small
screens. Raw HTML is disabled and remote Markdown images are shown as alt text.
Presentation can be checked with mocked API responses without paid model calls.


## My Life


Open **My Profile > My Life** to save your current home city/area, office
city/area, work arrangement, and up to ten household members. Each member has
a name or nickname, relationship, likes, dislikes, and dietary needs. All fields
are optional except the name and relationship of an added member. Remove a member
and save to delete them; clear any other fields and save to remove those details.
Cancel restores the last saved form. Saving makes no LLM calls.


These explicitly entered details are stored separately from inferred traits.
Sample loading and profile rebuilds do not overwrite them. Personalized Chat
and comparison prompts include this context and instruct Nemotron to use it only
when relevant, distinguish home from office and travel, ask for missing location,
and keep each person's preferences separate. Baseline prompts and all current MCP
tools exclude My Life data. Personalized requests do send this context to Nebius;
it is not a promise of on-device processing. This demo remains single-user and
should not be exposed publicly with real household information.


The backend creates the additional `life_contexts` table at startup without
altering existing tables or deleting data. `GET /api/profile/my-life` returns
the current object; `PUT /api/profile/my-life` replaces it with validated data.
Keys: `home_city`, `home_area`, `office_city`, `office_area`, `work_arrangement`,
and `household_members` (members: `name`, `relationship`, `likes`, `dislikes`,
`dietary_needs`). Missing fields default to empty; unknown fields, wrong types,
blank member names/relationships and overlong values return HTTP 422 without
changing saved data. City/area fields allow 120 characters, member names and
relationships 80, and other text fields 300. Account data deletion removes this
context too. There is no automatic residence inference. Optional public web
search can supply sources, but does not verify live availability.


## Optional Tavily web search


Chat and See the Difference display **Powered by Tavily** and offer an optional
**Use web search** control. Search is off by default. When enabled, enter a
separate **Public search query**, then submit your chat question or comparison.
Only that public query goes to Tavily: profile traits, My Life data, emails,
Calendar data, chat questions and conversation history are not automatically
included in search requests. Do not enter private data into the public query.


Set `TAVIL_API_KEY` in the backend environment and restart the backend.
`TAVILY_API_KEY` is also accepted; `TAVIL_API_KEY` takes precedence if both exist.
Never put either key in frontend/Vite configuration. No new package is needed:
the backend uses the existing HTTP client.


Each opted-in submission makes at most one basic Tavily search, with three
results, bounded snippets, a ten-second request timeout, no redirects and no
automatic retries. This uses Tavily credits, separate from the Nebius cost
ledger/daily cap. No extra LLM call generates or rewrites the search query.
See the Difference uses the **same single search result set for both answers**;
only the personalized answer receives saved profile/My Life context.


Nemotron receives the public snippets as untrusted evidence, is instructed not
to follow embedded commands, and should cite source links without inventing
availability. Source links are displayed below searched answers. Empty results
are explicitly labeled **not web-verified**. Missing configuration, provider
errors, exhausted credits and malformed/unsafe source URLs stop the request
before model generation instead of silently substituting a non-web answer.
Search snippets can be stale; verify booking availability and opening hours.


API: `/api/chat` and `/api/chat/compare` accept optional `web_query` (1-300
characters, containing non-whitespace). Omit it for no search. Chat retains its
plain-text streaming body, exposing `X-Web-Search-Used` and JSON
`X-Web-Sources` headers alongside `X-Used-Trait-Ids`. Compare responses include
`web_search_used` and `sources`. Citations contain title/URL only, not snippets.
Search results/queries are not stored in separate database records; generated
chat text, including any citations the model writes, retains existing storage
behavior. Tavily's own data handling still applies to submitted public queries.


Gmail/Calendar sync, profile processing, Calendar learning and Today checks run
directly from their buttons, without native browser confirmation dialogs.
Clicking an AI action explicitly submits its existing consent-bearing request.
Costs are disclosed beside the controls; expandable details at the end of each
connection card and Today panel explain privacy, batch limits and retries.
Connection descriptions lead with benefits, not limits. Destructive-action
confirmations and Google's OAuth permissions remain. The rebrand preserves the existing database filename,
demo-user identity, credentials and saved data; do not rename the database to
match the display name.


Backend validation: from `backend`, run
`.\.venv\Scripts\python.exe -m pytest tests\test_routes.py -q -k my_life`.


## MCP Ask approvals


Run the API, frontend, and standalone MCP server against the same database.
Restart the API and MCP processes after updating: startup creates the new
`mcp_approvals` table without deleting existing data.


1. A client calls `request_private_info` with a trait category such as `fitness`.
   Only Ask-tier traits are eligible; Private traits and My Life are excluded.
2. The tool returns `status: pending`, `request_id`, a secret `receipt`, and a
   five-minute expiry. No trait values are returned yet.
3. While the frontend is open, it checks every five seconds and shows a Pending
   requests badge and an in-app status notification on every tab. Click the badge
   to review; Privacy & MCP expands the review panel automatically. There are no
   operating-system, email, push, or closed-browser notifications.
4. Review the exact values and click **Allow once** or **Deny**. Client names are
   self-reported labels, not authenticated identities. Approval does not change
   trait privacy tiers. No action means no disclosure.
5. The client calls `check_approval` with `request_id` and `receipt` using the
   same bearer credential. A pending/denied/expired request returns status only.
   An approved request returns the reviewed snapshot once, then becomes consumed.
   If a response is lost after consumption, create a new request, not a replay.


The receipt is a per-request capability: keep it in the requesting client, never
paste it into shared logs or chat. Only its hash is stored; the dashboard never
receives it. Binding uses the current bearer credential plus the receipt, not
the spoofable client name. Clients sharing both credentials cannot be distinguished;
this is not per-client OAuth. Token rotation, trait edits/deletion/tier changes,
and expiry invalidate uncollected requests. Expiry is measured from creation and
is not extended by approval. Account deletion removes requests.


The original MCP call does not wait for the user. Clients must check again
after approval; automatic assistant resumption is not guaranteed. Avoid checking
faster than every five seconds. Current APIs:


- `GET /api/mcp/approvals`: latest 100 requests for the current demo user.
- `POST /api/mcp/approvals/{id}/decision`: `{"decision":"allow"}` or
  `{"decision":"deny"}`. Returns HTTP 409 for a no-longer-pending request.


All approval operations are free of LLM calls. The API still uses a single demo
user without real dashboard authentication; run locally and do not expose the
dashboard publicly. Future multi-user deployment requires real dashboard auth,
per-client credentials, rate limiting, and authenticated consent flows.


## Live Gmail (local development)


Gmail and Google Calendar support live read-only connections. Spotify, Strava
and Trakt still use samples; their Connect controls are disabled.
Use **Load sample data** for the demo. Sample loading never replaces an existing
live Google credential or marks that connection as Demo.


### Setup and connection


Enable Gmail API in your Google Cloud project, configure External/Testing
consent with your test account, and add only
`https://www.googleapis.com/auth/gmail.readonly`. Create a **Web application**
OAuth client with this exact authorized redirect:
`http://localhost:8000/api/oauth/callback/gmail`.


In the backend `.env`, configure `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REDIRECT_URI`, and a persistent `TOKEN_ENCRYPTION_KEY` (Fernet key).
`FRONTEND_URL` defaults to `http://localhost:5173`. Never commit these credentials.
Keep the encryption key stable after saving tokens. If a key has been shared,
replace it locally before connecting; existing tokens encrypted by an old key
must be reconnected or the original key restored.


Start the backend from its directory with
`.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
Run the frontend with `npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`.
Use **http://localhost:5173** consistently: the backend authorization URL and
callback must use the same hostname so the browser-bound cookie is returned.
Do not mix `127.0.0.1` and `localhost` in OAuth URLs.


Click **Connect** on the Gmail card, sign in on Google, and grant read-only
permission. The backend validates a hashed, browser-bound, single-use state
(ten-minute expiry) and PKCE verifier, exchanges the code, checks granted scope,
identifies the mailbox, encrypts tokens, and redirects back with a fixed result.
Neither connection nor callback imports emails or calls Nemotron.


Offline access requires a refresh token. If Google does not issue one, reconnect
and grant consent again; if necessary, remove the app's existing Google Account
permission and reconnect. External/Testing Gmail refresh tokens typically expire
after seven days, and Workspace policies can block consent. These are provider
restrictions, not permission to bypass consent. Public distribution requires a
separate review of Google's restricted-scope verification and data-use policies.


### Bounded manual Sync


**Sync Gmail** reads up to **25 messages** matching
receipt/invoice/booking/reservation terms **or action/deadline terms** within a
**30-day** window, excluding spam/trash and chats. Action terms include `deadline`,
`EOD`, `assignment`, `due today`, `due tomorrow`, `please review`, `please submit`,
`please send` and `please complete`. Manual sync revisits the bounded window so
tasks that predate a receipt-only sync can still be found. This is keyword matching, not guaranteed
discovery of every task. Pagination is bounded. This is a limited snapshot import,
not full mailbox sync or a durable history cursor. Repeated syncs deduplicate
by mailbox and message ID. If there are
more than 25 matches, older messages may remain outside this version's import.


Stored data: sender, subject (each capped at 500 characters), snippet (1,000),
message timestamp, and up to 6,000 characters of decoded plain text. Attachment
endpoints are never called; attachment parts and HTML bodies are ignored.
Read-only scope still authorizes broader mailbox access than these import filters.
Messages are fetched using Gmail's full-message format to inspect MIME parts;
the content bounds apply to stored text, not the total HTTP response size.


Tokens refresh near expiry, with one refresh/retry if Google rejects an early
expired access token. Network, permission, and quota failures are explicit;
failed live sync never silently substitutes samples. No partial message batch is
saved if fetching fails. Sync does not send emails to Nebius and makes no LLM
calls. Imported emails are unprocessed raw items, shown as awaiting AI processing;
they do **not** automatically become profile facts or change Chat answers.
The existing **Rebuild profile** processes existing facts only, not these new raw
emails. Use the separately consented **Process imported emails** action
described below. Do not treat demo traits as facts from your real inbox.


Rebuild cooldown timestamps loaded from SQLite are interpreted as UTC when the
driver omits timezone information. This avoids an internal server error on
subsequent rebuilds. A request inside the 30-second cooldown returns HTTP 429
with readable error details instead. After updating this behavior, restart the
backend. A browser CORS message accompanying HTTP 500 can mask a server exception;
check the backend traceback rather than disabling browser security.


**Reconnect** updates credentials only for the same mailbox. Disconnect before
switching Google accounts to avoid reusing one mailbox's refresh token for another.
**Disconnect** revokes the grant at Google and removes local tokens, but retains
imported emails and profile data. Gmail and Calendar connections for the same
Google account disconnect together because Google's app grants can be shared.
Different connected Google accounts are not disconnected together.
If revocation cannot be confirmed, the UI shows
an error instead of pretending success; you can also revoke access in your Google
Account permissions. **Delete all my data** removes local records and tokens but
does not itself revoke Google's grant; disconnect first or revoke it at Google.


API: `GET /api/connect/gmail`, `GET /api/oauth/callback/gmail`,
`POST /api/sync/gmail`, `DELETE /api/connections/gmail`. Connection listings
include mailbox email and the number of unprocessed live items, never tokens.
Startup creates `oauth_states` and `gmail_accounts` tables without changing or
deleting existing tables. The demo remains single-user without dashboard login:
bind locally and do not expose its API or dashboard publicly.


Tests use mocked HTTP transports and isolated databases; run
`.\.venv\Scripts\python.exe -m pytest tests\test_connectors.py tests\test_routes.py -q`
from the backend directory. Actual consent and mailbox access require a manual
test with your account and are not exercised by these tests.


### Batched Gmail classification, extraction and compact activity


On Home's Gmail card, clicking **Process imported emails** directly starts
processing the newest pending live emails (one to five, not sample data).
Paid processing is disclosed beside the controls, with privacy and limits in
expandable details rather than a blocking popup.
For each email it sends subject (500 characters), snippet (1,000), and body
text (4,000) to Nebius. Sender fields and attachments are excluded, but personal
details may still be present in the text. No automatic background processing.


`POST /api/gmail/process-batch` requires `{"consent":true}`:


1. One Nano call classifies up to five unclassified emails. Output must contain
   each submitted ID exactly once, with a supported event type. Profile category
   is derived in code, not independently guessed.
2. Save classifications in `email_ingestions` before extraction. `food_order`,
   `sports_booking`, and `travel_booking` route to distinct prompts and strict
   schemas. Promotions/irrelevant content are skipped; unknown/cancelled/refund
   content is held for review, not treated as purchases.
3. One **Super** extraction call per relevant email. Food retains dishes/restaurant;
   sports retains sport/venue; travel retains destination. Value/quote matching
   tolerates whitespace, HTML entities and `*`/backtick presentation markers, not
   changed names or arbitrary punctuation. Matched quotes are replaced with exact
   spans from the original submitted text. Booking/order identifier evidence is
   reconstructed from a real labeled source reference with the exact ID (including
   case), rather than requiring the model to reproduce label formatting.
   No free-form statement field:
   code constructs observations from allowed fields. Monetary/promo fields are
   absent from schemas and transactional text is rejected from activity values.
4. Persist each validated activity independently and mark its email processed.
   One failure does not roll back earlier successes. Failed extraction retains
   its classification; next explicit batch retries extraction without paying to
   classify that email again. Schema-valid candidate extractions are saved before
   evidence validation. On an evidence failure, subsequent batches revalidate that
   candidate locally without another extraction call. Candidates are bound to a
   hash of the submitted source fields; changed source requires review, not a
   silent paid retry. Existing failures from before candidate caching still need
   a new extraction unless recovered explicitly from available debug output.
   A budget failure stops further extraction calls.
5. Reconcile grouped preference cards across the whole category. Biryani variants
   contribute to one biryani preference; noodles/Manchurian contribute to Chinese
   food. Restaurant preferences use the food groups actually ordered there.
   Sports merge aliases (e.g. box cricket into cricket), with venue examples inside
   the sport card. When patterns change, both the leading and secondary cards
   change, retaining stable IDs. Exact item names and counts stay in Activity details.
   Travel retains its compact destination summary. No Ultra or additional synthesis call.


Maximum six calls per click: one Nano classification plus up to five Super
extractions. Food has a 2,048-output-token cap to accommodate semantic concepts;
other requests remain capped at 1,024. All use a 60-second SDK timeout,
reasoning disabled via `chat_template_kwargs.enable_thinking=false`, no SDK or
JSON-repair retries or automatic model fallbacks. Classification and category
extraction send their Pydantic-generated JSON Schema through
`response_format.type=json_schema` (strict) and include it in the prompt.
This follows [Nebius structured-output documentation](https://docs.tokenfactory.nebius.com/ai-models-inference/json).
The enriched schema was verified with five synthetic emails through the real
Nano/Super models and API on 2026-10-29 UTC (six calls, $0.002028 at configured
rates). An unsupported schema fails explicitly without falling back to plain JSON.
Local schema, exact-ID and evidence validation remain mandatory.


An identifier must be `{"value":"EXAMPLE123","quote":"Booking ID: EXAMPLE123"}`
(or a matching order reference), never a bare string. Use `null` when unavailable.
The prompt demonstrates both present and absent identifiers. Schema failures
report safe known field names (e.g. `order_id`) without echoing model values.


Each call checks the daily ledger with that model's rates, a conservative input
estimate (including schema payload) plus capped output. Super is priced higher:
the app currently uses 0.30/0.90 per million input/output tokens versus Nano's
0.06/0.24. Verify current provider pricing before relying on estimates.
This is not a whole-batch reservation, a lifetime
budget, or protection against simultaneous unrelated features spending money.
Provider-reported usage is logged before validation under `gmail_classification`
with model `nano`, or `gmail_food_order` / `gmail_sports_booking` /
`gmail_travel_booking` with model `super`. New extracted facts record `super`.
Missing usage is an explicit error; failed requests can still be charged.
The batch HTTP response includes per-email results/errors, including partial
success. Classification errors fail the batch before extraction; no labels saved.


The original `/api/gmail/process` remains a legacy single-email observation API
for compatibility. Home no longer calls it. Existing single-email cards and
processed emails are not automatically deleted, converted, or reprocessed.
Delete unwanted old cards manually; subsequent batches use the new flow.


For temporary local diagnosis, stop the API and set
`$env:GMAIL_NANO_DEBUG_OUTPUT="true"` in its PowerShell terminal before restarting
it normally. This works only with `APP_ENV=development`. On your next manually
consented processing request, the backend terminal emits a `GMAIL_NANO_DEBUG`
JSON log containing the task/stage, actual model/model ID, final model content
(up to 16,000 characters), finish
reason, token counts, and reasoning character count (not reasoning text).
It logs before validation, including malformed/truncated output or missing usage.
The historical `GMAIL_NANO_DEBUG` marker and environment flag are retained for
compatibility; they now also cover Super category extraction.
It does not log the input prompt, credentials, or full provider response, and
does not add output to browser errors or the database. Model output may still
contain private email data. Do not forward the unredacted log or enable this
where logs are collected externally; existing terminal/log tooling may retain it.
No new request is made just by enabling logging, and prior output cannot be
recovered. Another click is a new paid request. Afterwards stop the API, set
`$env:GMAIL_NANO_DEBUG_OUTPUT="false"`, and restart it. The default is off.


#### Activity counts, privacy and review


Rankings cover **all imported structured activity**, not a rolling 30-day ranking.
Import itself still searches the last 30 days; retained activity does not expire
from rankings. They mean most **ordered/booked**, not most liked/attended.
Exact-name rankings merge case/Unicode/whitespace variants, not different menu
names or restaurant branches. Distinct orders, not item quantities, are counted.
Each restaurant includes its dish counts; venues include sport counts.


Preference cards apply a separate semantic layer. Super returns evidence-bound
`concepts` alongside extracted dishes, with a stable food group and a generic
dish family. Familiar historical names are normalized locally (chicken dum and
masaledar biryani become chicken biryani; paneer stays a secondary variety).
Opaque brand-specific dishes remain in evidence; without supported context they
stay unclassified and do not create meaningless preference cards. They still
count toward category activity, preventing falsely dominant known preferences.
Restaurant cards describe food ordered from those places rather than claiming
the restaurant specializes in that cuisine. Restaurant names alone
never establish a cuisine. These are private interpretations of ordering patterns,
not confirmed likes, dietary restrictions or restaurant specialization.


Wording is calculated from distinct events, never item quantities:
- One event: "Has ordered/booked" (insufficient evidence for a habit).
- A unique leading group with at least two events: "Most often orders/books".
- Other recurring groups with at least 20% of category activity: "Also orders/books".
- Less frequent recurring groups: "Occasionally orders/books".
- Ties never produce competing "most often" claims. Counts are per group; one
  mixed-cuisine order may support several groups but counts once within each.
- Up to three generic dish examples and bounded restaurant/venue examples are
  shown. A dominant variety gets "especially"; uncommon secondary varieties get
  "occasionally also tries". Exact menu names remain accessible in evidence.


Restaurant comparisons use only orders with an identified restaurant. All
affected cards are reconciled after processing and activity removal. No extra
model synthesis request is needed, and new semantic concepts are cached with
the extraction. Historical opaque names are not silently re-sent to a model.


Matching mailbox + event type + merchant/venue + exact order/booking ID dedupes
multiple receipt emails. The identifier is kept in private event data and hashed
for matching; it is never included in summary text. Source emails are retained.
Duplicate food receipts merge dish evidence. Without an explicit merchant-scoped
ID, keep separate activities rather than guessing that identical purchases are
duplicates. Travel currently has no merchant field and therefore cannot dedupe
across different message IDs. Refunds/cancellations require review; they do not
automatically reverse earlier activity. These are deliberate conservative limits.


Preference cards show short natural-language patterns; categories initially show
at most ten cards with **View all**. **Activity details and ingestion review** contains all
rankings, per-restaurant/per-venue details and removable activity history.
Removing an activity excludes all its duplicate receipts and recalculates
summaries, retaining raw emails. Later receipts with the same merchant-scoped
identifier remain excluded. Affected user-edited/shared snapshots are retained
but reset Private with a review notice, not silently rewritten. Unknown/skipped items can be requeued there
without a model call; paid work still needs Home consent. Failed extractions can
be retried directly from Home using cached labels.


Facts and preference traits default Private. Preference confidence starts at
0.3 and rises by 0.04 per additional distinct supporting event, capped at 0.85;
this is an evidence-strength heuristic, not a calibrated probability. Cards have evidence links
and synthesized facts (no later automatic Ultra promotion). Change a reviewed
summary to Ask for personalized Chat or Share for MCP. **Editing a summary or
changing its privacy freezes that snapshot** using the existing user-override
flag; newly imported evidence never silently expands a previously shared
snapshot. Private Activity details continue updating. Deleting a summary blocks
its automatic recreation without deleting the underlying activity.


**Refresh preference cards (no model calls)** calls
`POST /api/profile/activity/refresh` to replace unreviewed old activity leaderboards
using saved events. It does not re-extract emails, spend model tokens, or touch
sample/legacy single-email cards. Reviewed snapshots remain unchanged. Old
dimension-level deletions/snapshots suppress their replacement preferences;
deleting a new preference suppresses that stable group key. A batch with no
pending emails also refreshes saved preferences for free.


## Live Google Calendar (local development)


Calendar v1 reads only the **primary calendar**, with manual sync. It has two
independent paths: free structured Today reminders and optional paid, private
profile learning. Connecting or syncing never calls Nemotron and never writes
to Google Calendar. No extra application source files or dependencies are needed.


### Setup


1. Enable **Google Calendar API** in the existing Google Cloud project.
2. Keep the existing Web application OAuth client ID, secret and persistent
   encryption key. Add the read-only Calendar scope to the consent configuration:
   `https://www.googleapis.com/auth/calendar.readonly`.
   Keep the existing Gmail scope and add your account as a test user if needed.
3. Add a second authorized redirect URI to the same OAuth client:
   `http://localhost:8000/api/oauth/callback/gcal`. Keep the Gmail redirect too.
4. In the backend `.env`, add
   `GOOGLE_CALENDAR_REDIRECT_URI=http://localhost:8000/api/oauth/callback/gcal`.
   It also defaults to this value. Custom hosts/ports must match the Google
   authorized redirect exactly. Keep using the same browser/backend hostname.
5. Restart the backend, refresh the app, and click Calendar **Connect**.
   Grant read-only access, then explicitly click **Sync Calendar**.


Calendar uses the same browser-bound, expiring, single-use OAuth state and PKCE
protections as Gmail, with a different callback, cookie and provider-bound hash.
Tokens are encrypted at rest; account switching requires disconnect first.
The scope permits broader calendar reading, but this implementation requests
only the primary calendar. Testing-mode refresh-token/provider restrictions
described for Gmail also apply here.


### Bounded manual sync and reminders


Sync covers **today plus the next six calendar days** in the primary calendar's
IANA timezone, with DST-aware midnight boundaries. It expands recurring events
into individual instances and reads at most **100 entries**, with bounded
pagination. This is a snapshot, not an incremental cursor or a full calendar
backup. Review the partial-sync notice if the cap is reached.


Stored fields: event ID, bounded title, dates/times, status, all-day flag,
timezone, event type and a validated Google event link. Descriptions, locations,
attendee identities, organizer details and conference URLs are not requested.
Only attendee `self`/RSVP status is requested to suppress declined invitations.
Confirmed events create direct Today reminders; tentative, declined, cancelled
and missing events do not. Timed events show a countdown until their start.
All-day/multi-day events show **All day**, using an exclusive end date, not an
invented midnight countdown.


Stable instance IDs reconcile updates/reschedules and preserve manual
Done/Dismiss choices across syncs. A complete snapshot withdraws previously
saved overlapping events that disappear from that exact window. A partial
snapshot **never** treats omitted events as deleted. All pages are fetched and
validated before local events are changed. Network, permission, scope and
malformed-date errors are explicit, with no sample fallback. Local times need
an explicit offset or IANA timezone; ambiguous/nonexistent DST wall times fail
instead of guessing.


Reminders reflect the **last manual sync**; changes made in Google after that
are not known until you sync again. There is no background polling, notification
permission, browser push, automatic sending or action completion. Calendar and
Gmail reminders are not fuzzy-merged: without a reliable shared identifier they
may describe the same event separately.


### Optional profile learning


**Learn from Calendar** starts directly when clicked, with a paid-AI disclosure
and expandable privacy/limit details on the card. It checks up
to five saved eligible events in **at most one Super call** per click, capped at
2,048 output tokens. Only titles (at most 500 characters each) and local event IDs
are sent to Nebius. Dates/times are interpreted locally; descriptions, attendee
identities and Google account IDs are not sent. No Ultra/synthesis/fallback
requests or automatic retries. Usage is logged as `calendar_learning` and the
existing daily budget gate applies. Validation/development uses mocks, not paid
requests.


The strict output must contain exactly the submitted IDs and title-backed
activity quotes. Recognized sports/exercise, work meetings/blocks and study
sessions produce generic **private** planned-pattern cards, e.g.
“Schedules badminton on Friday, in the afternoon.” The model is instructed to
ignore ambiguous/sensitive titles; review outputs before relying on them.
Cards never claim attendance or infer personality, medical status or confirmed
likes. Calendar schedule counts stay separate from Gmail booking/order counts.


Candidates are saved before validation and bound to the source fingerprint.
An identical invalid response can be rechecked locally without paying again.
New events are considered before failed saved candidates. No-response/network
or truncated responses may still be charged; an explicit retry can incur a
new request. Development output debugging can include private Calendar model
output too; keep `GMAIL_NANO_DEBUG_OUTPUT` disabled outside temporary debugging.


Changed/cancelled events withdraw their learned facts. Unedited cards
reconcile locally; reviewed snapshots retain their wording but lose removed
evidence and reset to Private. Learning changed events requires a separate
consented action. Unchanged ignored/learned events are not paid for again.
Blocked/deleted cards stay blocked. Saved Calendar observations appear in
Activity details and can be removed there; removed observations are not
relearned on sync. Sample cards and Gmail preference logic remain unchanged.


API:
- `GET /api/connect/gcal`, `GET /api/oauth/callback/gcal`.
- `POST /api/sync/gcal`: free bounded sync and reminder reconciliation.
- `POST /api/gcal/learn`: `{"consent":true}`; one optional bounded Super batch.
- `DELETE /api/connections/gcal`: revoke access, retaining imports/cards/reminders.


Disconnect warns that Gmail/Calendar for the **same Google account** are
disconnected together. A revocation failure is not reported as success.
**Delete all my data** also clears Calendar account/learning records and source
data, but does not revoke Google's server-side grant.
Startup creates `calendar_accounts` and `calendar_learning` without altering
existing SQLite columns. The shared per-user processing lease protects Google
sync/disconnect, Calendar learning and Gmail processing from overlap.


Official references: [event listing and recurrence](https://developers.google.com/workspace/calendar/api/v3/reference/events/list),
[primary calendar metadata](https://developers.google.com/workspace/calendar/api/v3/reference/calendars/get),
[Google OAuth and revocation](https://developers.google.com/identity/protocols/oauth2/web-server).
Actual Google consent/account access still needs your manual test; automated
tests isolate HTTP, model responses and storage and do not access your account.


## Today assistant (v1)


A compact bottom-right **Today** button shows counts of pending actions and
upcoming reminders for the browser's local day/timezone. It opens a small panel:
action titles contain three or four words and expand to a short explanation,
an **Open email** link, **Done**, **Snooze 30m**, and **Not an action**. Reminders
show only an event name and countdown (or **All day** for Calendar), with source
and dismiss controls. Calendar reminders come from the separate free sync above;
the email-check button never triggers Calendar fetches or learning.
The panel never opens automatically; minimizing it hides task text.


**Check imported emails** is a separate explicit action that starts directly
when clicked, without a confirmation popup. A paid-AI disclosure stays visible;
**How Today works** contains processing/privacy details at the panel's end.
It checks up to five already-imported **live Gmail** emails, including ones
already processed for profile preferences. It does not import more messages,
read attachments, or change raw-item profile processing flags. Gmail sync now
includes action/deadline requests alongside receipts/bookings. Sync first to
import new requests, then check Today. Assignment/review emails cannot appear
unless they have actually been imported. Sample emails are not sent to paid models.


One Super request per uncached email detects dated requests addressed to the
recipient and confirmed scheduled meetings/games. Each request sends the same
bounded subject/snippet/body fields as profile extraction, plus the receipt
timestamp and browser timezone context. Requests have a 2,048-output-token cap,
60-second timeout, thinking disabled, daily budget checks and usage logging
under `gmail_today`. At most five paid calls per click, no classifier/synthesis
calls, fallback calls, sending/submitting actions or automatic retries.
Do not enable output debugging where private model results are collected externally.


The model must supply source quotes for the request/event and its date/time.
Date-only actions are allowed; email reminders require an explicit time. Relative
dates use the email's receipt date, not the date the check runs. Missing or
ambiguous deadlines must not be invented. Local validation rejects unsupported
evidence, contradictory ISO/numeric/relative dates, weekday/time mismatches,
ambiguous numeric dates and ambiguous daylight-saving wall times. Textual
date interpretation still depends on the model; review extracted items before
relying on them. Explicit offsets/common timezone labels are checked against
the source clock; an unlabeled time uses the browser timezone supplied at scan.
`tzdata` supplies IANA timezone data on Windows.


An undated `EOD`/`end of day` request uses the local email receipt day.
If another date is supplied (for example `EOD tomorrow`), that date takes
precedence. EOD actions stay date-only unless an exact clock time is supplied:
the model must not invent 5 PM or 23:59. Missing receipt timestamps cannot ground
relative deadlines. Time ranges such as `9:30 AM-11:30 AM` are not timezone
offsets; explicit ISO/UTC offsets still apply. Booking/task reference IDs are
checked against real labeled source text, with exact ID case retained, rather
than rejecting a valid ID because the model formatted its label differently.
These validation fixes also apply to saved candidates on the next explicit
check without paying to extract them again.


Only today's pending dated items are visible—no overdue-from-yesterday section,
upcoming view, free-form chat or permanent personality traits. A timed reminder
disappears once its start time passes; Calendar all-day reminders remain through
their date range, and a due-today action remains until completed,
dismissed, snoozed, or the local day ends. Future dated items are saved privately
and appear on their day without another model call. No date means no Today item.
Unread/opened email does not mean an action is completed.


Output candidates are saved before schema/evidence validation and bound to the
original source. A validation failure can be rechecked locally on the next
explicit check without another model request. Changed source requires review,
not an automatic paid retry. New emails are checked before failed candidates.
Network failures with no saved response may still be charged; explicit retries
may incur another call.


Explicit reference IDs are scoped to mailbox, sender and item kind for duplicate
and dated cancellation/completion updates; action titles also scope task IDs.
Newer dated source messages take precedence over older confirmations. Manual
Done/Dismiss choices persist across duplicate receipt checks. A cancellation
arriving before its original confirmation leaves a non-pending record so the
older confirmation cannot recreate a reminder. Without a reliable reference,
separate emails are not guessed to be the same event. Undated/unmatched
cancellations and reference-less reschedules may need manual dismissal; this is
not a Gmail/Calendar cross-source reconciliation service.


API:
- `GET /api/today?timezone_name=Asia%2FKolkata`: saved local items/counts only.
- `POST /api/today/process`: `{"consent":true,"timezone_name":"Asia/Kolkata"}`.
- `PATCH /api/today/{id}`: `{"action":"done"}`, `"dismiss"` or `"snooze"`.


Opening/refreshing, countdowns and item controls make **zero model calls**.
Saved local data refreshes once per minute while the app is visible and on
focus; no Gmail fetch or model processing runs in the background. API/load
failures are shown explicitly rather than reporting a misleading zero count.
Today data is user-scoped and separate from profile traits, Chat and MCP.
**Delete all my data** removes Today items and saved scan candidates too.


Startup creates `email_ingestions`, `today_scans` and `today_items` without altering existing SQLite tables.
Account deletion clears ingestion states. A shared 15-minute per-user lease
prevents concurrent legacy/batch/Today processing requests; after a crash wait for expiry before
processing again. No automatic retries after network failure or partial success.


This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.


Currently, two official plugins are available:


- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)


## React Compiler


The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).


## Expanding the Oxlint configuration


If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:


```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```


See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.




