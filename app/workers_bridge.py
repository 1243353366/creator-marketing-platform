"""Bridge between the Flask platform and the live Cloudflare
Workers edition (https://github.com/1243353366/creator-marketing-
platform, deployed with a Durable Object store and Workers AI).

The Workers edition runs JavaScript on Cloudflare's edge; this
platform is Python. This module is the bridge: it calls the
Worker's REST API and maps responses into the platform's variant
shape, preserving the remote's honest source labeling end to end
(AI output is labeled AI, template output is labeled template -
the bridge never relabels).

Like every provider in the chain: a configured bridge that
fails raises - it never silently falls back to the template.
"""
import json
import socket
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 10


class BridgeError(Exception):
    """Bridge failure with an honest status, mapped 1:1 to the
    platform's GenerationError statuses (never swallowed)."""

    def __init__(self, status, detail):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def _request(url, tenant_id, path, body):
    req = urllib.request.Request(
        url + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "X-Tenant-Id": tenant_id})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
        return json.loads(r.read(65536).decode())


def bridge_generate(app_cfg, payload, tenant_id,
                    platform="blog"):
    """Call the live Worker's /api/generate and map the result
    to the platform's hook/caption/cta variant shape.

    Returns a variant dict whose source is 'bridge-ai' plus the
    remote's own source and label, preserved for the audit log.
    Raises BridgeError on any failure - no silent fallback.
    Returns None when the bridge is not configured (the caller
    then uses the labeled template, as before).
    """
    url = (app_cfg.get("WORKERS_BRIDGE_URL") or "").rstrip("/")
    if not url:
        return None
    topic = (f"{payload['objective']} for "
             f"{payload['audience']} (voice: {payload['voice']})")
    try:
        data = _request(url, "platform-" + str(tenant_id),
                        "/api/generate",
                        {"topic": topic, "platform": platform})
    except urllib.error.HTTPError as e:
        if e.code in (429, 402):
            raise BridgeError(
                "over_budget",
                f"bridge HTTP {e.code}") from e
        if e.code == 422:
            raise BridgeError(
                "rejected",
                "bridge content policy refused it") from e
        raise BridgeError(
            "provider_unavailable",
            f"bridge HTTP {e.code}") from e
    except (TimeoutError, socket.timeout):
        raise BridgeError("provider_timeout",
                          f"{TIMEOUT_SECONDS}s exceeded") from None
    except urllib.error.URLError as e:
        raise BridgeError("provider_unavailable",
                          f"unreachable: {e}") from e
    except ValueError as e:
        raise BridgeError("malformed_output", "bad JSON") from e

    content = data.get("content") or ""
    if not isinstance(content, str) or not content.strip():
        raise BridgeError("malformed_output", "empty content")
    lines = [ln.strip() for ln in content.strip().splitlines()
             if ln.strip()]
    hook = lines[0][:200] if lines else ""
    caption = (" ".join(lines[1:]) or hook)[:1000]
    return {
        "hook": hook,
        "caption": caption,
        "cta": payload.get("cta") or "Subscribe today (18+ only)",
        # honest labeling, end to end:
        "source": "bridge-ai",
        "remote_source": data.get("source", "unknown"),
        "labeled": bool(data.get("labeled")),
    }
