"""SEO auditing with SSRF-hardened fetching. Only public http(s)
destinations are ever contacted; private, loopback, link-local,
reserved, and metadata addresses are refused on every hop."""
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import unescape

from .db import session
from .models import SeoAudit, SeoPage

MAX_BYTES = 262144
TIMEOUT = 6
UA = "CreatorMarketingBot/1.0 (+seo audit)"
SCHEMES = ("http", "https")


class FetchRefused(Exception):
    pass


def _resolved_ips(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchRefused(f"DNS: {exc}") from exc
    return {i[4][0] for i in infos}


def _refused_ip(ip_text):
    try:
        addr = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    return (addr.is_private or addr.is_loopback
            or addr.is_link_local or addr.is_reserved
            or addr.is_multicast or addr.is_unspecified
            or addr.is_global is False)


def assert_public_url(url):
    if "://" not in url:
        raise FetchRefused("absolute http(s) URL required")
    scheme, rest = url.split("://", 1)
    if scheme not in SCHEMES:
        raise FetchRefused(f"scheme {scheme} not allowed")
    host = rest.split("/", 1)[0].split("@")[-1].split(":")[0]
    if not host:
        raise FetchRefused("no host")
    for ip in _resolved_ips(host):
        if _refused_ip(ip):
            raise FetchRefused(f"{host} resolves to a blocked "
                               f"address ({ip})")
    return host


class _ValidatingRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.hops = []

    def redirect_request(self, req, fp, code, msg, headers, data):
        target = headers.get("Location", "")
        host = assert_public_url(target)
        self.hops.append(target)
        if len(self.hops) > 4:
            raise FetchRefused("too many redirects")
        return super().redirect_request(
            req, fp, code, msg, headers, data)


def fetch(url):
    """Fetch a public page or raise FetchRefused/HTTPError."""
    assert_public_url(url)
    opener = urllib.request.build_opener(_ValidatingRedirect())
    req = urllib.request.Request(
        url, headers={"User-Agent": UA})
    with opener.open(req, timeout=TIMEOUT) as r:
        body = r.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise FetchRefused("response too large")
        return r.status, r.geturl(), body.decode(
            "utf-8", errors="replace")


META_ROBOTS = re.compile(
    r'<meta[^>]+name=["\']robots["\'][^>]*>', re.I)
META_CONTENT = re.compile(r'content=["\']([^"\']*)["\']', re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]*>', re.I)
CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]*>', re.I)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)


def _meta_content(tag):
    m = META_CONTENT.search(tag)
    return unescape(m.group(1)).strip() if m else ""


def audit_page(user_id, url):
    """Full single-page audit. Stores the result and returns the
    audit dict. Network errors are recorded as audits too."""
    db = session()
    page = db.query(SeoPage).filter(SeoPage.user_id == user_id,
                                    SeoPage.url == url).one_or_none()
    if page is None:
        page = SeoPage(user_id=user_id, url=url)
        db.add(page)
        db.commit()
    issues = []
    result = {"url": url, "http_status": 0, "redirect_ok": True,
              "robots_ok": True, "canonical_ok": False,
              "meta_title": "", "meta_desc": ""}
    try:
        status, final_url, html = fetch(url)
    except (FetchRefused, urllib.error.URLError,
            urllib.error.HTTPError) as exc:
        detail = str(exc)[:300]
        issues.append({"sev": "high", "code": "fetch_failed",
                       "detail": detail})
        row = SeoAudit(user_id=user_id, page_id=page.id, url=url,
                       http_status=0, redirect_ok=False,
                       robots_ok=False, canonical_ok=False,
                       issues_json=json.dumps(issues))
        page.last_status = "error"
        page.last_checked = datetime.now(timezone.utc)
        db.add(row)
        db.commit()
        result["error"] = detail
        return result
    result["http_status"] = status
    title = TITLE_RE.search(html)
    result["meta_title"] = unescape(
        title.group(1)).strip()[:300] if title else ""
    desc = DESC_RE.search(html)
    result["meta_desc"] = _meta_content(
        desc.group(0))[:500] if desc else ""
    robots_tag = META_ROBOTS.search(html)
    robots_content = _meta_content(
        robots_tag.group(0)).lower() if robots_tag else ""
    if "noindex" in robots_content:
        issues.append({"sev": "high", "code": "noindex",
                       "detail": "meta robots forbids indexing"})
        result["robots_ok"] = False
    if not result["meta_title"]:
        issues.append({"sev": "medium", "code": "missing_title",
                       "detail": "no <title> found"})
    if len(result["meta_title"]) > 60:
        issues.append({"sev": "low", "code": "long_title",
                       "detail": "title longer than 60 chars"})
    canon = CANONICAL_RE.search(html)
    if canon:
        href = HREF_RE.search(canon.group(0))
        if href:
            result["canonical_ok"] = True
    else:
        issues.append({"sev": "low", "code": "no_canonical",
                       "detail": "no canonical link"})
    row = SeoAudit(user_id=user_id, page_id=page.id, url=url,
                   http_status=status,
                   redirect_ok=True,
                   robots_ok=result["robots_ok"],
                   canonical_ok=result["canonical_ok"],
                   meta_title=result["meta_title"],
                   meta_desc=result["meta_desc"],
                   issues_json=json.dumps(issues))
    page.last_status = "ok" if not issues else "issues"
    page.last_checked = datetime.now(timezone.utc)
    db.add(row)
    db.commit()
    result["issues"] = issues
    return result


def generate_sitemap(urls):
    if not urls:
        raise ValueError("no urls")
    for u in urls:
        assert_public_url(u)
    items = "".join(
        f"<url><loc>{u}</loc></url>" for u in urls)
    return ("<?xml version='1.0' encoding='UTF-8'?>\n"
            f"<urlset xmlns='http://www.sitemaps.org/schemas/"
            f"sitemap/0.9'>{items}</urlset>")


def validate_sitemap(xml_text):
    urls = re.findall(r"<loc>(.*?)</loc>", xml_text)
    problems = []
    if "<urlset" not in xml_text:
        problems.append("missing <urlset> root")
    for u in urls:
        if not u.startswith(("http://", "https://")):
            problems.append(f"bad loc: {u[:60]}")
    return urls, problems
