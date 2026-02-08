# Subscription & Usage System -- Frontend Integration Guide

## Overview

The backend now enforces per-user subscription tiers with usage quotas. Three tiers exist:

| Tier | Worlds | Nodes | Reset Period |
|------|--------|-------|-------------|
| FREE | 3 | 30 | 7 days |
| EXPLORER | 20 | 500 | 30 days |
| COSMONAUT | 100 | 2,000 | 30 days |

All existing endpoints continue to work as before. The key changes are:
- Two existing endpoints (`POST /worlds/` and `POST /worlds/{id}/nodes/{id}/generate-text`) now enforce quotas and may return new error types.
- Three new endpoints provide usage info and Stripe session management.
- The JWT now carries `custom:tier` and `custom:stripe_customer_id` claims (available after the user subscribes and re-authenticates).

---

## New Endpoints

All new endpoints are under `/auth` and require the standard `Authorization: Bearer <token>` header.

### GET /auth/usage

Returns the authenticated user's current tier, usage counters, limits, and cancellation state.

**Response `200 OK`:**

```json
{
  "tier": "EXPLORER",
  "nodes_used": 142,
  "nodes_limit": 500,
  "worlds_created": 5,
  "worlds_limit": 20,
  "period_end": "2026-03-07T00:00:00+00:00",
  "pending_cancellation": false,
  "cancellation_date": null,
  "subscription_status": "active"
}
```

**Field descriptions:**

| Field | Type | Description |
|-------|------|-------------|
| `tier` | `string` | One of `"FREE"`, `"EXPLORER"`, `"COSMONAUT"` |
| `nodes_used` | `int` | Node generations consumed in the current period |
| `nodes_limit` | `int` | Maximum node generations allowed for the tier |
| `worlds_created` | `int` | Worlds created in the current period |
| `worlds_limit` | `int` | Maximum worlds allowed for the tier |
| `period_end` | `string \| null` | ISO 8601 datetime when the current usage period resets. Counters (`nodes_used`, `worlds_created`) reset to zero at this time. |
| `pending_cancellation` | `bool` | `true` when the user has cancelled but the subscription hasn't ended yet |
| `cancellation_date` | `string \| null` | ISO 8601 datetime of when the subscription will actually end (only set when `pending_cancellation` is `true`) |
| `subscription_status` | `string \| null` | Raw Stripe subscription status: `"active"`, `"past_due"`, `"unpaid"`, `"paused"`, or `null` for FREE-tier users who have never subscribed |

**Notes:**
- If the user has never been seen before, the backend automatically creates a FREE-tier record on first call.
- Counters reset lazily -- the first API call after `period_end` passes will reset them. The response always reflects the post-reset state.
- This endpoint is the source of truth for quota display. The JWT `custom:tier` claim may be stale if the user recently changed tiers without re-authenticating.

---

### POST /auth/checkout

Creates a Stripe Checkout Session to subscribe to a paid tier. Returns a URL to redirect the user to Stripe's hosted checkout page.

**Request body:**

```json
{
  "tier": "EXPLORER",
  "success_url": "https://cosmonaut-ai.com/settings?checkout=success",
  "cancel_url": "https://cosmonaut-ai.com/settings?checkout=cancelled"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tier` | `"EXPLORER" \| "COSMONAUT"` | Yes | Which plan to subscribe to |
| `success_url` | `string` | Yes | URL Stripe redirects to after successful payment |
| `cancel_url` | `string` | Yes | URL Stripe redirects to if the user abandons checkout |

**Response `200 OK`:**

```json
{
  "checkout_url": "https://checkout.stripe.com/c/pay/cs_live_..."
}
```

**Errors:**

| Status | Condition |
|--------|-----------|
| `400` | No Stripe price configured for the requested tier (server misconfiguration) |
| `502` | Stripe API error creating the checkout session |

**Flow:**
1. Frontend calls `POST /auth/checkout` with the desired tier and redirect URLs.
2. Backend creates a Stripe Checkout Session with the user's ID and email attached.
3. Frontend redirects the user to `checkout_url`.
4. User completes payment on Stripe's hosted page.
5. Stripe redirects the user to `success_url`.
6. In the background, Stripe fires a webhook to `POST /webhooks/stripe` which updates the user's tier, resets their counters, and syncs Cognito.
7. Frontend should call `GET /auth/usage` after the user lands on the success page to confirm the new tier.

---

### POST /auth/billing-portal

Creates a Stripe Billing Portal Session where the user can manage their subscription. Returns a URL to redirect the user to.

**Request body:** None (empty POST).

**Response `200 OK`:**

```json
{
  "portal_url": "https://billing.stripe.com/p/session/..."
}
```

**Errors:**

| Status | Condition |
|--------|-----------|
| `400` | User has no `stripe_customer_id` (i.e., they have never subscribed). The message is `"No active subscription found"`. |
| `502` | Stripe API error creating the portal session |

**What the user can do in the Billing Portal:**
- Cancel their subscription (Stripe may set `cancel_at_period_end` or `cancel_at` depending on portal configuration -- both are handled; the user keeps access until the cancellation date)
- Un-cancel a pending cancellation (clears `pending_cancellation` and `cancellation_date`)
- Change plan (upgrade or downgrade between EXPLORER and COSMONAUT)
- Update payment method

All changes made in the portal are processed by Stripe, which fires webhook events. The backend webhook handler updates DynamoDB and Cognito accordingly. No additional frontend API calls are needed for these actions.

**Flow:**
1. Frontend calls `POST /auth/billing-portal`.
2. Frontend redirects the user to `portal_url`.
3. User makes changes in the Stripe-hosted portal.
4. Stripe redirects the user back (to a return URL configured in the Stripe dashboard).
5. Frontend should call `GET /auth/usage` to pick up any changes.

---

## Changed Behavior on Existing Endpoints

### POST /worlds/ (Create World)

Now enforces the worlds quota before creating a world.

**New error response -- `429 Too Many Requests`:**

```json
{
  "detail": "Quota exceeded: worlds limit is 3"
}
```

This is returned when the user has reached their tier's world creation limit for the current period.

---

### POST /worlds/{world_id}/nodes/{node_id}/generate-text (Generate Story Text)

Now enforces the nodes quota before generating text for a node.

Because this is a streaming (SSE) endpoint, the quota error is delivered as an SSE error event rather than an HTTP status code:

```
event: error
data: Quota exceeded: nodes limit is 30
```

This error event fires at the start of the stream, before any `data:` text chunks are sent.

---

## JWT Claims

The Cognito JWT now includes two additional custom claims (after the user subscribes and the webhook syncs Cognito):

| Claim | Type | Description |
|-------|------|-------------|
| `custom:tier` | `string` | `"FREE"`, `"EXPLORER"`, or `"COSMONAUT"` |
| `custom:stripe_customer_id` | `string` | Stripe customer ID (e.g., `"cus_..."`) |

**Important caveats:**
- These claims are populated when the JWT is issued. If a user upgrades/downgrades mid-session, the JWT still carries the old tier until they re-authenticate (refresh the token or log out/in).
- For quota enforcement, the backend always uses the DynamoDB record, not the JWT. The JWT tier is safe to use for client-side UI hints (e.g., showing/hiding premium features, displaying tier badges) but should not be treated as authoritative for limits.
- For users who existed before the subscription system, `custom:tier` will be absent from the JWT. The backend defaults to `"FREE"` in this case.

---

## Subscription Lifecycle

### New subscription

```
User clicks "Subscribe" → POST /auth/checkout → redirect to Stripe Checkout
→ payment succeeds → Stripe webhook fires → backend updates tier + Cognito
→ user redirected to success_url → frontend calls GET /auth/usage
```

### Renewal (automatic)

```
Stripe charges the card → webhook fires → backend resets counters and extends period
→ no frontend action needed (GET /auth/usage will reflect new period on next call)
```

### Cancellation (by user via Billing Portal)

```
User clicks "Cancel" in portal → Stripe sets cancel_at_period_end=true or cancel_at
→ webhook fires → backend sets pending_cancellation=true, cancellation_date,
  subscription_status="active"
→ GET /auth/usage now shows pending_cancellation=true with cancellation_date
→ user keeps full access until cancellation_date
→ when cancellation date arrives, Stripe deletes subscription → webhook fires
→ backend downgrades to FREE, syncs Cognito
```

Note: Stripe may use either `cancel_at_period_end=true` or `cancel_at=<timestamp>` depending on portal configuration. The backend handles both.

### Cancellation reversal (user un-cancels via Billing Portal)

```
User clicks "Reactivate" in portal → Stripe clears cancel_at_period_end and cancel_at
→ webhook fires → backend clears pending_cancellation and cancellation_date
→ GET /auth/usage shows pending_cancellation=false, subscription_status="active"
```

### Failed payment (Stripe retrying)

```
Stripe charges card → payment fails → invoice.payment_failed webhook fires
→ backend sets subscription_status="past_due"
→ GET /auth/usage shows subscription_status="past_due"
→ frontend should display a payment warning banner with link to billing portal
→ user keeps full access while Stripe retries
```

### Failed payment (after Stripe exhausts retries)

```
All retries fail → Stripe either:
  (a) cancels subscription → customer.subscription.deleted webhook
      → backend downgrades to FREE
  (b) marks as "unpaid" → customer.subscription.updated webhook
      → backend downgrades to FREE
```

### Plan change (via Billing Portal)

```
User changes plan in portal → Stripe updates subscription
→ webhook fires → backend updates tier, resets counters, syncs Cognito
→ GET /auth/usage reflects new tier on next call
```

---

## Webhook Endpoint (Backend-Only, Not Called by Frontend)

`POST /webhooks/stripe` is called by Stripe's servers, not by the frontend. Documented here for completeness.

- No authentication header required (uses Stripe signature verification).
- Handles: `checkout.session.completed`, `invoice.payment_succeeded`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`.
- Always returns `200 OK` to Stripe (even on internal errors, to prevent Stripe retries for transient failures).

---

## Recommended Frontend Patterns

### When to call GET /auth/usage

- On app load / after login (to populate the usage state).
- After returning from Stripe Checkout or Billing Portal (to pick up tier/quota changes).
- Before displaying quota-sensitive UI (e.g., "Create World" button).
- Optionally: periodically poll if you want near-real-time quota display.

### Handling 429 on world creation

When `POST /worlds/` returns `429`, surface a user-friendly message like "You've reached your world limit for this period. Upgrade your plan or wait for your period to reset." with a link to the upgrade flow (`POST /auth/checkout`) or the billing portal (`POST /auth/billing-portal`).

### Handling quota SSE errors on node generation

Listen for `event: error` in the SSE stream from `POST /worlds/{id}/nodes/{id}/generate-text`. If the data starts with `"Quota exceeded"`, display an upgrade prompt instead of a generic error.

### Showing cancellation state

When `GET /auth/usage` returns `pending_cancellation: true`, display messaging like:
> "Your EXPLORER plan will end on {cancellation_date}. You'll be downgraded to the Free plan after that."

With a CTA to manage their subscription (via `POST /auth/billing-portal`) where they can reactivate.

### Showing payment issues

When `GET /auth/usage` returns `subscription_status: "past_due"`, display a warning banner like:
> "There's an issue with your payment. Please update your payment method to avoid losing access."

With a link to the billing portal (`POST /auth/billing-portal`).

### Handling `subscription_status` values

| Status | Meaning | Recommended UI |
|--------|---------|----------------|
| `"active"` | Subscription is healthy | Normal UI |
| `"past_due"` | Payment failed, Stripe is retrying | Yellow warning banner with link to update payment method |
| `"unpaid"` | All retries exhausted, downgraded to FREE | User is on FREE tier; show re-subscribe prompt |
| `"paused"` | Subscription paused | Informational banner, link to billing portal |
| `null` | No subscription (FREE-tier, never subscribed) | Show upgrade prompts |

### Disabling the billing portal button for free users

Only show the "Manage Subscription" button (which calls `POST /auth/billing-portal`) when the user's tier is not `"FREE"`. The endpoint returns `400` if the user has never subscribed.
