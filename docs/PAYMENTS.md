# Payment architecture

## Three flows, three rule sets

| Flow | Payer | Payee | Who configures |
|---|---|---|---|
| SaaS subscription | creator | us (software) | operator |
| Creator checkout | fan | creator | creator |
| Creator payout | platform/processor | creator | operator |

These are separate transactions with separate provider rules. One
provider is not assumed to permit all three, and a wallet button is
never treated as a verified payment.

## Provider policy matrix (verify at integration time)

Recorded 2026-10; policies change. This table is a guardrail, not
legal advice. The code enforces it structurally in
`app/payments.py:PROVIDER_FLOW_POLICY` — a provider refused for a
flow cannot be configured for it, secret or not.

| Provider | SaaS | Creator checkout | Payout | Notes |
|---|---|---|---|---|
| Stripe | yes | NO | yes | restricts adult-content sales |
| Segpay | - | candidate | candidate | apply for the exact model |
| CCBill | - | candidate | candidate | verify marketplace eligibility |
| Verotel | - | candidate | candidate | country coverage, fees |
| Authorize.Net | possible | - | - | acquiring bank must accept adult |
| PayPal | possible | NO | - | prohibits adult digital content |
| Cash App | NO | NO | NO | prohibits adult content and related |
| Venmo | - | - | - | verify current policy first |
| Wise | - | - | possible | permitted business purpose |
| ACH/wire | possible | - | possible | underwriting, reconciliation |
| Crypto | - | - | possible | compliance, volatility, custody risk |

## Non-negotiable rules

1. Marketplace warning: a processor that supports adult merchants
   may still reject a platform that collects money for third-party
   creators. Get WRITTEN approval for the marketplace model before
   collecting creator funds. Until then this platform never holds
   creator money; creators connect their own approved processor
   and we link out (`BrandProfile.checkout_url`, https-only).
2. Unlock rule: content or paid features unlock ONLY after a
   server-verified payment record (`verified_payments` table) from
   a signed webhook or provider API check. A submitted handle,
   screenshot, or client-side success message never verifies.
3. Credential rule: no card numbers, CVVs, bank passwords, Cash
   App PINs, or wallet keys are ever stored. Provider secrets live
   in the deployment environment only.
4. Fail-closed rule: an unconfigured flow refuses to create a
   checkout (visible error, setup pointer). Nothing is faked.

## Wiring a provider

1. Confirm the provider's CURRENT published policy permits the
   exact flow and business model, in writing.
2. Set `PAYMENT_<PROVIDER>_SECRET` in the deployment environment.
3. Register the adapter:
   `payments.configure_provider(payments.CREATOR_CHECKOUT, "segpay", app.config)`.
4. Point the provider's webhook at `/payments/webhook`
   (HMAC-verified, replay-protected, idempotent — implemented and
   tested in `tests/test_platform.py`).
5. Verify a sandbox end-to-end payment before production.
