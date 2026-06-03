# downthestretch.ai — Web Layer Architecture (v0.2)

_Date: 2026-05-27 · Supersedes v0.1 · Companion to `PROJECT_REVIEW.md`_

This is a revision of the v0.1 sketch, grounded in the code that actually exists in `dts-web/` rather than a generic stack proposal. The original v0.1 recommended Clerk + Cloudflare R2 + Neon Postgres + Resend on Vercel; the actual stack is Supabase + Vercel Blob + Vercel hosting, with substantial Phase 1 code already shipped. This v0.2 builds on what's there instead of redesigning around it.

## TL;DR

The auth/payment "Phase 5" is narrower than v0.1 implied. The hard parts — private object storage, a pipeline-to-site upload contract, the entitlements schema, server-side blob pipe-through reads — are already done. What's missing is roughly: a Supabase Auth login replacing the shared-password gate; a Stripe webhook that writes rows into the existing `entitlements` table; a `has_access()` SQL function as the single gating predicate; a gate check added to the read route (which currently doesn't authorize); and three user-facing surfaces (dashboard, morning email, inline viewer).

## What's already shipped (Phase 1)

**Stack.** Next.js 16.2.6 (App Router, React 19), TypeScript, Tailwind 4, deployed on Vercel at downthestretch.ai. `@supabase/supabase-js` for the database, `@vercel/blob` for PDF storage. The repo lives at `github.com/RyanRitz/dts-web`. `AGENTS.md` warns that Next 16 has breaking changes from older versions — read `node_modules/next/dist/docs/` before writing new framework code.

**Storage.** PDFs are stored in a Vercel Blob bucket with `access: "private"`. The blob URLs themselves require an `Authorization: Bearer ${BLOB_READ_WRITE_TOKEN}` header to fetch, so even leaked URLs can't be opened by a browser directly. `lib/supabase-admin.ts` returns a service-role client used for all admin-tier DB writes.

**Schema.** `supabase/migrations/20260518000000_phase1_schema.sql` is the source of truth and already designs the full entitlement taxonomy. Three tables:

- `sheets (filename PK, track, race_date, label, blob_url, uploaded_at, superseded_at)` — index over Vercel Blob. Partial unique index enforces one live row per `(track, race_date, label)`.
- `meets (meet_id PK, name, track, start_date, end_date)` — maintained by hand via the Supabase dashboard.
- `entitlements (user_id → auth.users, type, track, race_date, start_date, end_date, meet_id, stripe_payment_id)` where `type ∈ {daily, window_7, window_30, window_365, meet}`. A `CHECK` constraint enforces which columns are meaningful per type. Composite index on `(user_id, end_date, race_date)` covers the gate query.

All three tables have RLS enabled with **no policies** — the safe default. The service-role key bypasses RLS, so the upload route and the read route work; anything reaching Supabase from a browser sees nothing.

**Upload + cleanup contract** (`app/api/sheets/upload/route.ts`, `app/api/sheets/cleanup/route.ts`). The pipeline POSTs PDFs with an `X-DTS-Upload-Secret` header, multipart fields `file/track/race_date/label`. Filename is canonicalized to `YYYYMMDD-TRACK-LABEL.pdf` and validated against the client-provided fields. PREVIEW uploads are auto-superseded when a FINAL arrives — the FINAL upload deletes the old PREVIEW blob and marks its row `superseded_at = now()`. A separate cleanup endpoint runs nightly from the pipeline machine and enforces a 3-day rolling retention. This whole chain works today and shouldn't change in Phase 5.

**Phase 1 access gate** (`app/sheets/page.tsx`, `app/api/sheets/auth/route.ts`, `lib/access-cookie.ts`). The `/sheets` page is a Server Component that lists every live sheet from the last 3 days behind a shared password (`DTS_SHEETS_PASSWORD`). The auth route accepts the password, mints a 30-day signed cookie (HMAC-SHA256 over the expiry timestamp, keyed with `DTS_UPLOAD_SECRET`), and sets it httpOnly+secure+SameSite=Lax. Comments mark this as throwaway code, explicitly to be replaced in Phase 5 by real auth.

**Read route** (`app/sheets/[filename]/route.ts`). Validates the filename, looks up the sheet in Supabase, and streams the private blob bytes back to the user — server-side fetch with the bearer token, response body piped through. The user never sees the blob URL or the token. PREVIEW→FINAL supersession redirects are handled here too. Headers set `X-Robots-Tag: noindex, nofollow` and `Cache-Control: private, max-age=0, must-revalidate`. **There is no authorization check on this route today** — anyone who knows or guesses the filename can fetch the PDF. URLs follow a predictable pattern (`YYYYMMDD-{KEE,SAR,DMR,...}-{PREVIEW,FINAL}.pdf`) over a known set of ~20 tracks and a 3-day window, so the URL space is enumerable. This is the single biggest gap for a paid launch.

## Phase 5 — what changes

Five things, in dependency order:

### 5a. Replace the shared-password gate with Supabase Auth

Drop `DTS_SHEETS_PASSWORD`, the cookie scheme in `lib/access-cookie.ts`, the auth route at `app/api/sheets/auth/route.ts`, and the password form half of `app/sheets/page.tsx`. Replace with Supabase Auth configured for two methods: passwordless magic link (primary, friendliest for low-frequency users) and Google OAuth (one-click for users who dislike email links). Both are built into Supabase, no extra provider account needed.

On the server side, use `createServerClient` from `@supabase/ssr` to read the session cookie in Server Components and Route Handlers. Wrap session-protected routes in a small `requireUser()` helper that returns the `auth.users.id` or redirects to `/login`. The migration from Phase 1's cookie is one-way — once Supabase Auth ships, the old cookie scheme stops being checked anywhere. Keep `DTS_UPLOAD_SECRET` for the pipeline→site upload path; that's a service-to-service secret, not user auth, and stays.

### 5b. Stripe Products + webhook → entitlements

Create five Stripe Prices, one per `entitlements.type`:

| SKU | Stripe shape | Entitlement row written on webhook |
|---|---|---|
| Day pass | One-time Price | `type='daily'`, `track`, `race_date` |
| 7-day pass | One-time Price | `type='window_7'`, `start_date=today`, `end_date=today+6` |
| Monthly subscription | Recurring Price | `type='window_30'`, period from Stripe |
| Annual subscription | Recurring Price | `type='window_365'`, period from Stripe |
| Meet pass | One-time Price (per meet) | `type='meet'`, `meet_id` |

The day pass and meet pass need the buyer to specify which track/date or which meet at Checkout time. The cleanest way is to pass these as Checkout Session `metadata`, which Stripe echoes back on the webhook. For meet passes specifically, you'd build one Stripe Product per meet (Keeneland April 2026, Saratoga Summer 2026, etc.) so the buyer sees a real meet name in Checkout. Each meet's Stripe Price ID is stored on the `meets` row.

New endpoint: `app/api/stripe/webhook/route.ts`. Verifies the Stripe signature, looks up `stripe_event_id` for idempotency, branches on event type (`checkout.session.completed` for one-time, `customer.subscription.created/updated/deleted` for recurring), and writes the appropriate `entitlements` row(s). Refunds (`charge.refunded`) need to either delete the row or — better — add a `refunded_at` column and have `has_access()` exclude it.

Stripe Checkout (hosted page) is the right starting point — faster to ship than Payment Element, less code to maintain, automatically handles SCA/3DS. Move to Payment Element later if checkout conversion data argues for it.

### 5c. `has_access()` — the single gate predicate

One Postgres function. Every read-side authorization check goes through it:

```sql
create or replace function public.has_access(
  p_user_id uuid,
  p_track   text,
  p_date    date
) returns boolean
language sql
stable
security definer
as $$
  select exists (
    select 1 from public.entitlements e
    where e.user_id = p_user_id
      and (
        (e.type = 'daily'  and e.track = p_track and e.race_date = p_date)
        or
        (e.type = 'meet'   and exists (
            select 1 from public.meets m
            where m.meet_id = e.meet_id
              and m.track   = p_track
              and p_date between m.start_date and m.end_date
        ))
        or
        (e.type like 'window_%' and p_date between e.start_date and e.end_date)
      )
  );
$$;
```

Centralizing the predicate means future SKU additions touch one function instead of every route. Add tests against it directly (Supabase supports `pgTAP` or just plain SQL assertions).

### 5d. Gate the read route — security-critical

This is the change that converts the site from "friends and family" to "paid product." `app/sheets/[filename]/route.ts` becomes:

```ts
const { user, supabase } = await requireUser(req);
if (!user) return new NextResponse("Unauthorized", { status: 401 });

const parsed = parseFilename(filename);
if (!parsed) return notFound();

const { data: allowed } = await supabase.rpc("has_access", {
  p_user_id: user.id,
  p_track:   parsed.track,
  p_date:    parsed.raceDate,
});
if (!allowed) return new NextResponse("Forbidden", { status: 403 });

// ... existing supersession + blob pipe-through code unchanged
```

Three behaviors to log for every call: `(user_id, filename, allowed, reason)`. That log is your audit trail when a subscriber says "I paid and can't see it" — and it makes refund/dispute discussions concrete.

The same pattern needs to apply to a future `/api/pdf-url` if you ever add one, and to the email-link landing page (see 5f).

### 5e, 5f, 5g — user-facing surfaces

**Dashboard** (`app/(member)/page.tsx` or replace `app/sheets/page.tsx`): logged-in landing page. Lists today's and the next two days' tracks. For each `(track, date)` shows: PDF status (PREVIEW / FINAL / not ready), whether the user has access (via `has_access`), and a Download button. Non-entitled users see a "Buy a day pass" or "Upgrade to monthly" CTA inline. Purchase history table near the bottom (`entitlements` filtered to the user). All Server Component, one or two Supabase queries.

**Morning email**: when `cleanup_dts.py` finishes on the pipeline side (or a new explicit "PDFs are live" trigger), the pipeline POSTs to a new endpoint on the website with the list of (track, date) pairs ready. The endpoint looks up every entitled user for each (track, date) — same `has_access` check, just inverted to "find users with access" — and queues one email per user via Resend. Email body has a "View today's sheets" link to the dashboard, NOT direct PDF links. Reason: direct links bypass the gate the moment the email is forwarded. The dashboard link re-checks entitlement on each click.

**Inline viewer** (`app/sheets/[filename]/view/page.tsx`): mounts PDF.js, fetches the PDF via the gated read route, renders in-browser with no download button. Useful as a piracy-resistant viewing mode and — crucially — as a free-preview path. Show race 1 of every card unauthenticated for SEO and conversion; require entitlement for the rest.

## Cross-cutting concerns

- **Email service**: recommend Resend. Generous free tier, react-email JSX templates, Vercel-friendly. Postmark is the alternative if you want stricter deliverability guarantees and don't mind plain HTML templates.
- **Tax**: turn on Stripe Tax before launch. US sales tax on digital goods varies by state and is annoying to track manually.
- **Refunds / disputes**: handle `charge.refunded` and `charge.dispute.created` webhooks. Add a `refunded_at` column to `entitlements` and exclude refunded rows in `has_access`.
- **Idempotency**: every Stripe webhook handler must dedupe on `stripe_event_id` (add a unique index). Stripe retries on non-2xx responses and you do not want duplicate entitlements.
- **Logging**: every read-route call should log `(user_id, filename, allowed, reason, latency_ms)` somewhere queryable. Supabase has built-in logs; Vercel has Logs. Either works; pick one and stay consistent.
- **Free preview / SEO**: from day one, expose at least one card per week as unauthenticated content (e.g., the race-1 PDF for a marquee track on Saturday). Without something to crawl, organic discovery is dead on arrival.
- **Repo privacy**: `github.com/RyanRitz/dts-web` is currently public. Make it private until launch.

## Migration: Phase 1 → Phase 5 (no big bang)

Five PRs, each small enough to ship and validate independently:

1. **Schema + Stripe products + webhook + `has_access`**. No UI yet. Migration adds `has_access()`, adds `refunded_at` to `entitlements`, adds the unique index on Stripe event IDs. Webhook handler with thorough tests against `has_access`. Stripe Products configured in test mode.
2. **Supabase Auth + `requireUser` helper**. `/login` page (magic link + Google), session cookie wiring, the helper. No paywall yet — the auth just gets installed alongside the existing password gate, with a feature flag to switch between them.
3. **Gate the read route**. Apply the `has_access` check to `app/sheets/[filename]/route.ts`. This is the security-critical change. Behind a feature flag initially, so you can flip it off if anything blows up.
4. **Dashboard + Checkout buttons + retire Phase 1 password**. Real member home page. Stripe Checkout links live. Flip the auth feature flag to "Supabase only," delete `lib/access-cookie.ts`, `app/api/sheets/auth/route.ts`, and the password half of `app/sheets/page.tsx`.
5. **Morning email + inline viewer**. Pipeline triggers email send; PDF.js viewer route. Free-preview cards configurable per-meet.

PRs 1–4 get you to paid launch. PR 5 is upgrade-quality.

## Open questions to answer before PR 1

- **Pricing.** Day pass $X, 7-day $Y, monthly $Z, annual $W, meet pass $V each. Numbers shape the SKU mix.
- **Trial?** A `window_7` granted on first signup gives free-trial-to-paid mechanics for nothing. Recommend yes.
- **Meet catalog source of truth.** The `meets` table is manual per the schema comment. Who maintains it, where's the list of upcoming meets to seed?
- **Initial subscribers.** Migrating users from the old BTSM, or fresh start? Migration would mean a one-off script that inserts `auth.users` rows + a `window_*` entitlement covering each existing customer's remaining term.
- **Domain for transactional email.** `@downthestretch.ai`? If yes, DKIM/SPF/DMARC setup is part of PR 5.
- **Pipeline trigger for the morning email.** New endpoint, or piggyback on `cleanup_dts.py`'s completion?

## Notable decisions vs v0.1

- **Storage:** Vercel Blob (kept) instead of Cloudflare R2 (proposed). Pipe-through-server reads instead of signed URLs. Better — no URL to leak.
- **Auth provider:** Supabase Auth (kept) instead of Clerk (proposed). Already integrated, free at scale, simpler vendor footprint.
- **DB:** Supabase Postgres (kept) instead of Neon Postgres (proposed). One vendor instead of two.
- **Schema:** Existing taxonomy (`daily | window_7 | window_30 | window_365 | meet`) instead of proposed (`day | meet | sub`). More general — `window_30` *is* monthly sub; `window_7` enables free trials cleanly.
- **Hosting:** Vercel (kept).
- **Email:** Resend (kept).
- **PDF viewer:** PDF.js (kept).
