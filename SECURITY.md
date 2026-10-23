# Security policy

## Reporting a vulnerability

Report privately to the repository owner via GitHub security
advisories ("Report a vulnerability"). Do not open a public
issue for security reports.

## Security boundary description

Trust boundaries:
1. Internet -> Flask app. All input is untrusted: forms, SEO
   URLs, webhook payloads, provider output.
2. App -> PostgreSQL. Parameterized queries only; tenant
   ownership enforced in every repository query by user_id
   taken from the server session.
3. App -> external HTTP (SEO fetch). Scheme allowlist (http/https
   only), DNS resolution with refusal of loopback, private,
   link-local, reserved, and multicast addresses, redirect
   revalidation, 6s timeouts, 256KB response cap.
4. App -> AI provider. Creator instructions are sent as data;
   provider output is re-validated server-side and treated as
   untrusted.
5. Payment provider -> app. HMAC-signed webhooks with replay
   protection and idempotent processing; redirects and
   client-side success messages are untrusted and never grant
   access.

Password storage: Argon2id (argon2-cffi) with per-hash salt and
constant-time verification. Session tokens: 256-bit random,
stored only as SHA-256 hashes, 12h expiry. CSRF tokens bound to
the session, required on every state-changing request.

Fail-closed configuration: missing DATABASE_URL, weak/missing
SECRET_KEY, or missing webhook secret disable the dependent
feature loudly. No mock mode exists in production paths.

## Threat model summary

See PLAN.md for the full STRIDE analysis with abuse cases and
residual risks.
