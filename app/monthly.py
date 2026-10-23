"""Monthly improvement pipeline. Deterministic, idempotent per
(tenant, period), evidence-linked, uncertainty-aware, and it never
changes production behavior without explicit approval."""
import json
from datetime import datetime, timezone

from .db import session
from .models import Campaign, Consent, Metric, MonthlyReview
from sqlalchemy.exc import IntegrityError

MIN_IMPRESSIONS = 100
Z = 1.96


def wilson(successes, n):
    """Wilson 95% interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denom
    half = Z * ((p * (1 - p) / n + Z * Z / (4 * n * n))
                ** 0.5) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _collect(user_id, period):
    """Consent-gated collection with duplicate elimination.
    Duplicates are grouped, not double-counted."""
    if not _has_analytics_consent(user_id):
        return None, "analytics consent not granted"
    year, month = int(period[:4]), int(period[5:7])
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = (datetime(year + (month == 12), (month % 12) + 1, 1,
                    tzinfo=timezone.utc))
    db = session()
    rows = db.query(Metric).filter(
        Metric.user_id == user_id,
        Metric.recorded_at >= start,
        Metric.recorded_at < end).all()
    seen, clean = set(), []
    anomalies = []
    for r in rows:
        key = (r.campaign_id, r.variant_label,
               r.window_start, r.window_end, r.source)
        if key in seen:
            anomalies.append({"code": "duplicate_event",
                              "campaign": r.campaign_id})
            continue
        seen.add(key)
        if r.impressions < r.clicks or r.clicks < r.conversions:
            anomalies.append({"code": "impossible_rates",
                              "campaign": r.campaign_id})
            continue
        clean.append(r)
    return clean, anomalies


def _has_analytics_consent(user_id):
    db = session()
    row = db.query(Consent).filter(
        Consent.user_id == user_id,
        Consent.kind == "analytics").one_or_none()
    return bool(row and row.granted)


def build_review(user_id, period):
    """Idempotent on (user, period): reruns return the existing
    proposal instead of duplicating."""
    db = session()
    existing = db.query(MonthlyReview).filter(
        MonthlyReview.user_id == user_id,
        MonthlyReview.period == period).one_or_none()
    if existing is not None:
        return existing, "already-exists"
    rows, problem = _collect(user_id, period)
    if rows is None:
        raise ValueError(problem)
    by = {}
    for r in rows:
        by.setdefault(
            (r.campaign_id, r.variant_label, r.source), []).append(r)
    comparisons = []
    grouped = {}
    for (campaign_id, label, src), rs in by.items():
        impr = sum(x.impressions for x in rs)
        clk = sum(x.clicks for x in rs)
        conv = sum(x.conversions for x in rs)
        rev = sum(x.revenue_cents for x in rs)
        spend = sum(x.spend_cents for x in rs)
        lo, hi = wilson(clk, impr)
        conv_lo, conv_hi = wilson(conv, clk)
        grouped[(campaign_id, label)] = {
            "impressions": impr, "clicks": clk,
            "conversions": conv,
            "ctr": round(clk / impr, 4) if impr else None,
            "ctr_ci": [round(lo, 4), round(hi, 4)],
            "conv_rate": (round(conv / clk, 4) if clk else None),
            "conv_ci": [round(conv_lo, 4), round(conv_hi, 4)],
            "revenue_cents": rev, "spend_cents": spend,
            "roas": (round(rev / spend, 2)
                     if spend else None),
            "source": src,
            "eligible_for_comparison": impr >= MIN_IMPRESSIONS}
    campaigns = {}
    db2 = session()
    for (cid, _label), stats in grouped.items():
        c = db2.query(Campaign).filter(
            Campaign.id == cid).one_or_none()
        if c:
            campaigns.setdefault(
                (c.channel, c.objective), []).append(
                (cid, _label, stats))
    for (channel, objective), group in campaigns.items():
        if len(group) < 2:
            continue
        eligible = [g for g in group
                    if g[2]["eligible_for_comparison"]]
        if len(eligible) < 2:
            comparisons.append({
                "channel": channel, "objective": objective,
                "note": "insufficient sample for a comparison "
                        f"(min {MIN_IMPRESSIONS} impressions "
                        "per variant); no winner is selected",
                "variants": [g[1] for g in group]})
            continue
        a, b = eligible[0], eligible[1]
        overlap = (a[2]["ctr_ci"][0] <= b[2]["ctr_ci"][1]
                   and b[2]["ctr_ci"][0] <= a[2]["ctr_ci"][1])
        comparisons.append({
            "channel": channel, "objective": objective,
            "variants": {"A": a[2], "B": b[2]},
            "intervals_overlap": overlap,
            "note": ("intervals overlap: the difference is not "
                     "statistically meaningful — do not pick a "
                     "winner on raw rates" if overlap else
                     "intervals are separated; a controlled "
                     "experiment may be justified"),
            "consider": (
                "Keep both variants running and re-evaluate next "
                "period with a larger sample." if overlap else
                "Run a predefined experiment with stopping "
                "criteria before adopting the change.")})
    proposal = {
        "period": period,
        "tenant": user_id,
        "generated_at": datetime.now(
            timezone.utc).isoformat(),
        "data_points": len(rows),
        "limitations": [
            "observational data: correlation is not causation",
            f"minimum sample {MIN_IMPRESSIONS} impressions "
            "before any comparison",
            "no revenue or growth outcomes are promised"],
        "metrics": {f"{cid}:{label}": stats
                    for (cid, label), stats in grouped.items()},
        "comparisons": comparisons,
        "risks": ["small samples overstate differences",
                  "seasonality is not modelled"],
    }
    review = MonthlyReview(user_id=user_id, period=period,
                           status="proposed",
                           proposal_json=json.dumps(proposal))
    db.add(review)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.query(MonthlyReview).filter(
            MonthlyReview.user_id == user_id,
            MonthlyReview.period ==
            period).one(), "already-exists"
    return review, "created"


def decide_review(user_id, review_id, decision):
    """Approval requires an authenticated owner of the review."""
    db = session()
    row = db.query(MonthlyReview).filter(
        MonthlyReview.id == review_id,
        MonthlyReview.user_id == user_id).one_or_none()
    if row is None or decision not in ("approved", "rejected"):
        raise PermissionError("review not found for this tenant")
    row.status = decision
    row.decided_at = datetime.now(timezone.utc)
    db.commit()
    return row
