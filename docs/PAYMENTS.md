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

## Fan-to-creator: options the CREATOR can use

These are the creator's own payment choices, not our processors.
We never touch this money; the creator connects their approved
platform and we link out.

| Option | Best use | Adult-content caveat |
|---|---|---|
| Cash App | U.S. transfers | PROHIBITED (verified 2026-10-23): the Acceptable Use Policy explicitly bars selling adult content and services, incl. pornography, pay-per-view, and adult live-chat; accounts may be suspended or terminated. cash.app/us/en/legal/acceptable-use-policy |
| Venmo | U.S. payments | PROHIBITED for this use (verified 2026-10-23): the U.S. User Agreement bars unauthorized commercial transactions on personal accounts and restricts obscene content / nudity / sexual activity. venmo.com/legal/us-user-agreement |
| PayPal | general online payments | RESTRICTED (verified 2026-10-23): the Acceptable Use Policy identifies digitally delivered adult content incl. VOD and webcam as restricted (US page: paypal.com/us/legalhub/paypal/acceptableuse-full). Relabeling does not exempt a transaction. |
| Zelle | bank-to-bank | not an adult-commerce solution; check terms |
| Direct bank / ACH | U.S. transfers | ask the bank about business use and transaction nature |
| Paxum | adult-industry payments | verify availability, eligibility, fees, withdrawals |
| Skrill | wallet, international | verify activity and country support |
| Crypto (BTC/USDC) | direct wallet payments | irreversible, volatile, tax obligations; USDC issuer controls |
| OnlyFans / Fansly / similar | subscriptions, tips, paid content | platform runs checkout and pays out via its own methods |

**Practical recommendation for creators** (shown on the billing
page): keep one approved creator platform for paid content, one
verified bank payout method, and a second eligible payout option.
Do not rely on Cash App or Venmo for adult-content transactions —
their policies prohibit them.

**Tips, gifts, and disguised purchases** (distinction verified
against published policies 2026-10-23):

1. Unconditional gift: money sent with nothing in return.
2. Tip: voluntary extra money, e.g. in appreciation of content.
3. Purchase disguised as a tip: payment with an expectation of
   receiving a photo set, custom video, private chat, or other
   service.

Case 3 is a commercial transaction even if the payment note says
"gift" or "tip." Changing the description does not change the
underlying transaction, and no provider policy found grants a
blanket exemption for relabeled purchases — Cash App's policy
explicitly does not. This platform therefore takes no position
that relabeling is safe: commercial transactions belong on the
creator's approved processor, full stop. For genuine gifts with
no exchange, the only reliable answer is to ask the provider
directly whether that specific use is allowed; we do not claim
unsolicited personal transfers are prohibited or permitted. No
tipping feature exists on this platform, and none would launch
without its own policy evaluation.

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
