"""Share utilities: keywords from the creator's OWN data, and
share links that carry the platform's referral.

Honesty rules (this module generates marketing suggestions):
  - Keywords are derived ONLY from the creator's own brand
    profile and page metadata. Nothing is invented; an empty
    profile yields NO keywords, never filler.
  - The referral is explicit and labeled: every share carries
    the platform's GitHub link so shared posts credit the
    tool. It is never hidden in the link text.
"""
from urllib.parse import quote

# The platform's own referral: shared posts credit this
# project's source. Win-win - the creator promotes their
# business, the platform travels with the post.
REFERRAL_URL = (
    "https://github.com/1243353366/"
    "creator-marketing-platform")
REFERRAL_LABEL = "Built with creator-marketing-platform (open source)"

# words never worth suggesting as keywords
_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have i in is it
    its me my not of on or our so the their them they this to
    we with you your""".split())

MAX_KEYWORDS = 12


def meta_share_url(page_url: str) -> str:
    """Meta (Facebook) share dialog URL for a page the CREATOR
    chose to share. The page url is quoted so an adversarial
    url cannot inject extra sharer parameters."""
    return ("https://www.facebook.com/sharer/sharer.php?u="
            + quote(page_url, safe=""))


def generate_keywords(profile, extra_text: str = "") -> list:
    """Deterministic keyword suggestions from the creator's own
    data: brand display name, voice, audience, cta, channels,
    plus any page metadata text. Returns at most MAX_KEYWORDS
    lowercase tokens (len>=3, no stopwords), de-duplicated in
    first-seen order. Empty inputs -> empty list. No filler."""
    if profile is None:
        return []
    parts = [profile.display_name, profile.voice,
             profile.audience, profile.cta,
             getattr(profile, "channels", ""),
             getattr(profile, "conversion_goal", "")]
    parts.append(extra_text or "")
    seen, out = set(), []
    for part in parts:
        if not part:
            continue
        for tok in part.lower().replace(",", " ").split():
            tok = "".join(c for c in tok if c.isalnum())
            if (len(tok) >= 3 and tok not in _STOPWORDS
                    and tok not in seen):
                seen.add(tok)
                out.append(tok)
                if len(out) >= MAX_KEYWORDS:
                    return out
    return out
