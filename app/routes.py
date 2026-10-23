"""HTTP layer. tenant identity comes ONLY from the session token;
IDs in requests are used only in tenant-filtered lookups."""
import json
import secrets

from flask import (Blueprint, abort, flash, redirect,
                   render_template, request, session as fsession,
                   url_for)

from . import auth, consent as consent_svc, marketing, monthly
from . import payments as payments_svc, seo
from .db import session as dbsession
from .models import (User, BrandProfile, Campaign, Consent,
                     Metric,
                     MonthlyReview, SeoAudit, SeoPage, Variant)

bp = Blueprint("ui", __name__)


def current_user():
    data = auth.user_for_token(fsession.get("token", ""))
    return data[0] if data else None


def require_user():
    user = current_user()
    if user is None:
        abort(401)
    return user


def check_csrf():
    row = auth.user_for_token(fsession.get("token", ""))
    if row is None:
        abort(401)
    _, sess = row
    if not secrets.compare_digest(
            request.form.get("csrf", ""), sess.csrf_token):
        abort(400, "CSRF token mismatch")


@bp.app_context_processor
def inject_user():
    data = auth.user_for_token(fsession.get("token", ""))
    return {"user": data[0] if data else None,
            "csrf": data[1].csrf_token if data else ""}


def register(app):
    app.register_blueprint(bp)


@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/healthz")
def healthz():
    db = dbsession()
    db.execute(__import__("sqlalchemy").text("select 1"))
    return {"ok": True}


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        if "@" not in email or len(password) < 10:
            flash("Valid email and 10+ char password required")
            return render_template("signup.html"), 400
        db = dbsession()
        if db.query(User
                    ).filter_by(email=email).one_or_none():
            flash("Email already registered")
            return render_template("signup.html"), 409
        user = User(email=email,
                    password_hash=auth.hash_password(password))
        db.add(user)
        db.commit()
        _, token = auth.login(email, password)
        fsession["token"] = token
        return redirect(url_for("ui.dashboard"))
    return render_template("signup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            _, token = auth.login(
                request.form.get("email", ""),
                request.form.get("password", ""),
                request.remote_addr or "-")
            fsession["token"] = token
            return redirect(url_for("ui.dashboard"))
        except PermissionError as exc:
            flash(str(exc))
            return render_template("login.html"), 401
    return render_template("login.html")


@bp.post("/logout")
def logout():
    auth.logout(fsession.get("token", ""))
    fsession.clear()
    return redirect(url_for("ui.index"))


@bp.get("/dashboard")
def dashboard():
    user = require_user()
    db = dbsession()
    campaigns = db.query(Campaign).filter_by(
        user_id=user.id).order_by(
        Campaign.created_at.desc()).all()
    return render_template("dashboard.html",
                           campaigns=campaigns)


@bp.route("/brand", methods=["GET", "POST"])
def brand():
    user = require_user()
    db = dbsession()
    if request.method == "POST":
        check_csrf()
        profile = db.query(BrandProfile).filter_by(
            user_id=user.id).one_or_none()
        fields = {k: request.form.get(k, "")[:2000]
                  for k in ("display_name", "voice", "audience",
                            "restrictions", "cta",
                            "conversion_goal", "channels")}
        try:
            fields["intensity"] = max(
                1, min(5, int(request.form.get("intensity", 2))))
        except ValueError:
            fields["intensity"] = 2
        if profile is None:
            db.add(BrandProfile(user_id=user.id, **fields))
        else:
            for k, v in fields.items():
                setattr(profile, k, v)
        db.commit()
        return redirect(url_for("ui.dashboard"))
    profile = db.query(BrandProfile).filter_by(
        user_id=user.id).one_or_none()
    return render_template("brand.html", profile=profile)


@bp.post("/campaign/new")
def campaign_new():
    user = require_user()
    check_csrf()
    db = dbsession()
    name = request.form.get("name", "")[:120].strip()
    if not name:
        abort(400)
    db.add(Campaign(
        user_id=user.id, name=name,
        channel=request.form.get("channel", "email")[:32],
        objective=request.form.get("objective",
                                   "subscriptions")[:32]))
    db.commit()
    return redirect(url_for("ui.dashboard"))


@bp.get("/campaign/<cid>")
def campaign_view(cid):
    user = require_user()
    db = dbsession()
    c = db.query(Campaign).filter_by(
        id=cid, user_id=user.id).one_or_none()
    if c is None:
        abort(404)
    variants = db.query(Variant).filter_by(
        campaign_id=c.id, user_id=user.id).all()
    return render_template("campaign.html", campaign=c,
                           variants=variants,
                           error=request.args.get("error", ""))


@bp.post("/campaign/<cid>/generate")
def campaign_generate(cid):
    user = require_user()
    check_csrf()
    try:
        marketing.generate(cid, user.id, current_app_config())
    except marketing.GenerationError as exc:
        return redirect(url_for(
            "ui.campaign_view", cid=cid, error=exc.detail))
    return redirect(url_for("ui.campaign_view", cid=cid))


def current_app_config():
    from flask import current_app
    return current_app.config


@bp.post("/campaign/<cid>/confirm")
def campaign_confirm(cid):
    user = require_user()
    check_csrf()
    try:
        marketing.confirm_publication(cid, user.id)
    except marketing.GenerationError as exc:
        abort(400, exc.detail)
    db = dbsession()
    db.query(Variant).filter_by(
        campaign_id=cid, user_id=user.id).update(
        {"published_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc)})
    db.commit()
    return redirect(url_for("ui.campaign_view", cid=cid))


@bp.route("/consent", methods=["GET", "POST"])
def consent_page():
    user = require_user()
    if request.method == "POST":
        check_csrf()
        consent_svc.set_consent(
            user.id, "analytics",
            request.form.get("analytics") == "on")
        consent_svc.set_consent(
            user.id, "training",
            request.form.get("training") == "on")
        return redirect(url_for("ui.consent_page"))
    return render_template(
        "consent.html",
        analytics=consent_svc.has_consent(user.id, "analytics"),
        training=consent_svc.has_consent(user.id, "training"))


@bp.post("/campaign/<cid>/metric")
def add_metric(cid):
    user = require_user()
    check_csrf()
    if not consent_svc.has_consent(user.id, "analytics"):
        abort(403, "analytics consent required")
    db = dbsession()
    c = db.query(Campaign).filter_by(
        id=cid, user_id=user.id).one_or_none()
    if c is None:
        abort(404)

    def num(key):
        return max(0, int(request.form.get(key, 0) or 0))
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    db.add(Metric(
        user_id=user.id, campaign_id=cid,
        variant_label=request.form.get("variant_label",
                                       "A")[:4],
        recorded_at=now,
        window_start=now, window_end=now,
        impressions=num("impressions"), clicks=num("clicks"),
        conversions=num("conversions"),
        revenue_cents=num("revenue_cents"),
        spend_cents=num("spend_cents"),
        source=request.form.get("source",
                                "manual")[:32]))
    db.commit()
    return redirect(url_for("ui.campaign_view", cid=cid))


@bp.route("/seo", methods=["GET", "POST"])
def seo_page_list():
    user = require_user()
    db = dbsession()
    if request.method == "POST":
        check_csrf()
        url = request.form.get("url", "")[:2048]
        try:
            seo.assert_public_url(url)
        except seo.FetchRefused as exc:
            flash(f"Refused: {exc}")
            return render_template("seo.html"), 400
        db.add(SeoPage(user_id=user.id, url=url))
        db.commit()
        return redirect(url_for("ui.seo_page_list"))
    pages = db.query(SeoPage).filter_by(
        user_id=user.id).all()
    return render_template("seo.html", pages=pages,
                          error=request.args.get("error", ""))


@bp.post("/seo/<page_id>/audit")
def seo_run_audit(page_id):
    user = require_user()
    check_csrf()
    db = dbsession()
    page = db.query(SeoPage).filter_by(
        id=page_id, user_id=user.id).one_or_none()
    if page is None:
        abort(404)
    result = seo.audit_page(user.id, page.url)
    if "error" in result:
        return redirect(url_for(
            "ui.seo_page_list", error=result["error"]))
    return redirect(url_for("ui.seo_page_list"))


@bp.get("/seo/audits")
def seo_audit_history():
    user = require_user()
    db = dbsession()
    audits = db.query(SeoAudit).filter_by(
        user_id=user.id).order_by(
        SeoAudit.checked_at.desc()).limit(100).all()
    return render_template(
        "audits.html", audits=[{
            "url": a.url, "status": a.http_status,
            "title": a.meta_title[:80],
            "issues": json.loads(a.issues_json),
            "at": a.checked_at} for a in audits])


@bp.post("/seo/sitemap")
def seo_sitemap():
    user = require_user()
    check_csrf()
    urls = [u.strip() for u in
            request.form.get("urls", "").splitlines() if u.strip()]
    try:
        xml = seo.generate_sitemap(urls)
    except (seo.FetchRefused, ValueError) as exc:
        return render_template("sitemap.html", xml="",
                               problems=[str(exc)]), 400
    _, problems = seo.validate_sitemap(xml)
    return render_template("sitemap.html", xml=xml,
                           problems=problems)


@bp.get("/seo/sitemap")
def seo_sitemap_form():
    return render_template("sitemap.html", xml="", problems=[])


@bp.route("/review", methods=["GET", "POST"])
def review():
    user = require_user()
    if request.method == "POST":
        check_csrf()
        period = request.form.get("period", "")
        if len(period) != 7 or period[4] != "-":
            abort(400)
        try:
            row, _ = monthly.build_review(user.id, period)
        except ValueError as exc:
            flash(str(exc))
            return render_template("review.html",
                                   reviews=[]), 403
        return redirect(url_for("ui.review"))
    db = dbsession()
    reviews = db.query(MonthlyReview).filter_by(
        user_id=user.id).order_by(
        MonthlyReview.period.desc()).all()
    return render_template(
        "review.html",
        reviews=[{"id": r.id, "period": r.period,
                  "status": r.status,
                  "proposal": json.loads(r.proposal_json)}
                 for r in reviews])


@bp.post("/review/<rid>/decide")
def review_decide(rid):
    user = require_user()
    check_csrf()
    decision = request.form.get("decision", "")
    if decision not in ("approved", "rejected"):
        abort(400)
    try:
        monthly.decide_review(user.id, rid, decision)
    except PermissionError:
        abort(404)
    return redirect(url_for("ui.review"))


@bp.post("/webhook/payment")
def payment_webhook():
    sig = request.headers.get("X-Signature", "")
    ts = request.headers.get("X-Timestamp", "")
    provider = request.headers.get("X-Provider", "unknown")
    event_id = request.headers.get("X-Event-Id", "")
    if not event_id:
        abort(400)
    try:
        payments_svc.verify_webhook(
            current_app_config(), provider, event_id, ts,
            request.get_data(), sig)
    except payments_svc.WebhookRefused as exc:
        abort(400, str(exc))
    return {"received": True}


@bp.get("/payments")
def payments_page():
    require_user()
    return render_template("payments.html",
                           status=payments_svc.provider_status(
                               current_app_config()))


@bp.errorhandler(401)
def _unauth(e):
    return redirect(url_for("ui.login"))
