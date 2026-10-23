"""Marketing generation engine. Sources are explicit and labeled:
- "provider": the configured external AI endpoint (optional)
- "template": a deterministic local composer, labeled as such
Provider failures are visible error states. Template output is
never presented as AI output, and provider failure never silently
substitutes anything."""
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from .db import session
from .models import BrandProfile, Campaign, GenerationLog, Variant

BANNED = ("underage", "minor", "preteen", "loli", "nonconsensual",
          "without consent", "non-consensual", "incest", "rape")
REVENUE_PROMISES = ("guaranteed income", "guaranteed revenue",
                    "you will earn", "guaranteed to make",
                    "guaranteed followers", "guaranteed growth",
                    "guaranteed results")
GEN_RATE_LIMIT = 30
GEN_WINDOW = timedelta(hours=1)


class GenerationError(Exception):
    """Raised with a machine-readable status matching
    GenerationLog.status — the UI shows it, nothing is faked."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _log(db, user_id, campaign_id, provider, status, detail=""):
    db.add(GenerationLog(user_id=user_id,
                         campaign_id=campaign_id,
                         provider=provider, status=status,
                         detail=detail[:500]))
    db.commit()


def check_banned(text):
    words = text.lower()
    hits = [b for b in BANNED if re.search(
        r"\b" + re.escape(b) + r"\b", words)]
    return hits


def validate_output(data):
    """Server-side output schema. Returns (variant_dict, errors)."""
    errors = []
    if not isinstance(data, dict):
        return None, ["output is not an object"]
    for field, limit in (("hook", 200), ("caption", 1000),
                         ("cta", 200)):
        v = data.get(field, "")
        if not isinstance(v, str) or not 1 <= len(v) <= limit:
            errors.append(f"{field} must be 1..{limit} chars")
    banned_hits = check_banned(" ".join(
        str(v) for v in data.values()))
    if banned_hits:
        errors.append(f"banned terms present: {banned_hits}")
    promises = [p for p in REVENUE_PROMISES if p in " ".join(
        str(v) for v in data.values()).lower()]
    if promises:
        errors.append(f"revenue promises present: {promises}")
    if errors:
        return None, errors
    return {"hook": data["hook"], "caption": data["caption"],
            "cta": data["cta"]}, []


def _profile_payload(profile, campaign):
    return {
        "voice": profile.voice,
        "audience": profile.audience,
        "intensity": profile.intensity,
        "restrictions": profile.restrictions,
        "cta": profile.cta,
        "channel": campaign.channel,
        "objective": campaign.objective,
        "instruction": ("Produce marketing copy. Never promise "
                       "specific revenue or growth outcomes. "
                       "Respect the restrictions. Adults only "
                       "where applicable."),
    }


def _call_provider(app_cfg, payload):
    """POST to the configured provider. All failure modes map to
    GenerationError statuses."""
    url = app_cfg.get("AI_PROVIDER_URL", "")
    key = app_cfg.get("AI_PROVIDER_KEY", "")
    if not url or not key:
        return None
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read(65536).decode())
    except urllib.error.HTTPError as e:
        if e.code in (429, 402):
            raise GenerationError(
                "over_budget", f"provider HTTP {e.code}")
        raise GenerationError(
            "provider_unavailable", f"provider HTTP {e.code}")
    except (TimeoutError, socket.timeout):
        raise GenerationError("provider_timeout", "10s exceeded")
    except urllib.error.URLError as e:
        raise GenerationError("provider_unavailable",
                              f"unreachable: {e}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise GenerationError("malformed_output", "bad JSON")


def template_variant(profile, campaign, label):
    """Deterministic, clearly-labeled local composition. This is
    not AI output and is never shown as such."""
    tone = {1: "understated and factual", 2: "warm and confident",
            3: "bold", 4: "very direct", 5: "maximally provocative"}
    return {
        "hook": f"{profile.display_name}: "
                f"{tone[profile.intensity]} content, made for you",
        "caption": (
            f"For {profile.audience}: {profile.voice} content on "
            f"{campaign.objective}. Subscribe to see more. "
            f"We never promise specific results."),
        "cta": profile.cta or "Subscribe today (18+ only)",
    }


def generate(campaign_id, user_id, app_cfg):
    """Generates A/B variants. Consent-independent but
    rate-limited; every attempt is logged with an honest status."""
    db = session()
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.user_id == user_id).one_or_none()
    if campaign is None:
        raise GenerationError("rejected", "unknown campaign")
    profile = db.query(BrandProfile).filter(
        BrandProfile.user_id == user_id).one_or_none()
    if profile is None:
        raise GenerationError("rejected", "complete brand profile "
                                      "first")
    hour_ago = datetime.now(timezone.utc) - GEN_WINDOW
    recent = db.query(GenerationLog).filter(
        GenerationLog.user_id == user_id,
        GenerationLog.at >= hour_ago).count()
    if recent >= GEN_RATE_LIMIT:
        _log(db, user_id, campaign_id, "engine", "rejected",
             "rate limit")
        raise GenerationError("rejected", "generation rate limit")
    payload = _profile_payload(profile, campaign)
    provider_out = _call_provider(app_cfg, payload)
    variants = []
    for label in ("A", "B"):
        if provider_out is not None:
            candidate = provider_out.get(label)
            if candidate is None:
                candidate = provider_out
            data, errors = validate_output(candidate)
            if errors:
                _log(db, user_id, campaign_id, "provider",
                     "rejected", "; ".join(errors))
                raise GenerationError("rejected",
                                      f"variant {label}: "
                                      + "; ".join(errors))
            source = "provider"
        else:
            data = template_variant(profile, campaign, label)
            source = "template"
        row = Variant(user_id=user_id, campaign_id=campaign_id,
                      label=label,
                      content_json=json.dumps(data),
                      source=source)
        db.add(row)
        variants.append(row)
    campaign.status = "generated"
    _log(db, user_id, campaign_id,
         "provider" if provider_out is not None else "template",
         "ok")
    return variants


def confirm_publication(campaign_id, user_id):
    """Explicit confirmation is required before a campaign is
    marked published. Publishing here means the creator accepted
    the content for use; the platform does not send externally."""
    db = session()
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.user_id == user_id).one_or_none()
    if campaign is None or campaign.status != "generated":
        raise GenerationError("rejected",
                              "generate before confirming")
    campaign.status = "confirmed"
    db.commit()
    return campaign
