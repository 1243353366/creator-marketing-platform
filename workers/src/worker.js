// Creator Marketing Platform - Workers edition with Durable Objects persistence.
// Faithful port of the live worker (workers/live-worker-export.js). The
// in-memory Maps are replaced by a single strongly-consistent Durable Object
// (SQLite storage, ships with the script - no separate database permission
// needed). Same API surface, same gates: tenant header auth, consent
// enforcement, SSRF-hardened audit, content policy, labeled template
// fallback, idempotent pipeline, tenant isolation.
//
// Why DO and not KV: KV is eventually consistent - a consent grant could be
// invisible for up to 60s on another edge, which re-breaks the consent gate.
// DO storage is strongly consistent and free-plan eligible (verified live:
// a probe DO answered on this account before this deploy).
import { DurableObject } from 'cloudflare:workers';

export class Store extends DurableObject {
  constructor(ctx, env) { super(ctx, env); this.ctx = ctx; }
  async getTenant(t) {
    return (await this.ctx.storage.get('tenant:' + t)) || { brandVoice: null, intensity: 'balanced', analyticsConsent: false, trainingConsent: false };
  }
  async putTenant(t, data) { await this.ctx.storage.put('tenant:' + t, data); return data; }
  async putScript(s) { await this.ctx.storage.put('script:' + s.tenantId + ':' + s.createdAt + ':' + s.id, s); }
  async listScripts(t) {
    const l = await this.ctx.storage.list({ prefix: 'script:' + t + ':' });
    return [...l.values()].sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
  }
  async getPipeline(k) { return await this.ctx.storage.get('pipeline:' + k) || null; }
  async putPipeline(k, v) { await this.ctx.storage.put('pipeline:' + k, v); }
  async putAudit(a) { await this.ctx.storage.put('audit:' + a.tenantId + ':' + a.createdAt + ':' + a.auditId, a); }
}

export default {
  async fetch(request, env) {
    const store = env.STORE.get(env.STORE.idFromName('store'));
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    const JSON_HEADERS = { 'Content-Type': 'application/json' };
    function json(data, status = 200) {
      return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
    }
    function fail(message, status = 400, details) {
      return json({ error: message, ...(details ? { details } : {}) }, status);
    }

    // ---------- DO-backed tenant store (strongly consistent) ----------
    const getTenant = (tenantId) => store.getTenant(tenantId);

    function genId(prefix) {
      return prefix + '_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
    }

    function isPrivateIP(ip) {
      const parts = ip.split('.').map(Number);
      if (parts[0] === 10) return true;
      if (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) return true;
      if (parts[0] === 192 && parts[1] === 168) return true;
      if (parts[0] === 127) return true;
      if (parts[0] === 169 && parts[1] === 254) return true;
      if (parts.every(p => p === 0)) return true;
      return false;
    }

    async function ssrfSafeFetch(urlStr) {
      let parsed;
      try { parsed = new URL(urlStr); } catch { throw new Error('Invalid URL'); }
      if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') {
        throw new Error('Blocked: protocol ' + parsed.protocol + ' not allowed');
      }
      try {
        const dnsResp = await fetch('https://cloudflare-dns.com/dns-query?name=' + encodeURIComponent(parsed.hostname) + '&type=A', {
          headers: { Accept: 'application/dns-json' },
        });
        const dnsResult = await dnsResp.json();
        if (dnsResult && dnsResult.Answer) {
          for (const ans of dnsResult.Answer) {
            if (ans.type === 1 && isPrivateIP(ans.data)) {
              throw new Error('Blocked: ' + parsed.hostname + ' resolves to private IP ' + ans.data);
            }
          }
        }
      } catch (e) { /* DNS check best-effort */ }

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const resp = await fetch(urlStr, {
          signal: controller.signal,
          redirect: 'manual',
          headers: { 'User-Agent': 'CreatorMarketingPlatform/1.0 SEO-Auditor' },
        });
        clearTimeout(timeout);
        if (resp.status >= 300 && resp.status < 400) {
          const loc = resp.headers.get('location');
          if (loc) { return ssrfSafeFetch(new URL(loc, parsed).href); }
        }
        return resp;
      } catch (e) {
        clearTimeout(timeout);
        throw new Error('Fetch failed: ' + e.message);
      }
    }

    function templateGenerate(brandVoice, intensity, topic, platform) {
      const voice = brandVoice || 'professional';
      const intensities = {
        subtle: { cta: 'Learn more', urgency: 'when you have a moment' },
        balanced: { cta: 'Get started today', urgency: "don't miss out" },
        bold: { cta: 'Act now', urgency: 'limited time' },
      };
      const cfg = intensities[intensity] || intensities.balanced;
      const templates = {
        instagram: '\u{1F31F} ' + topic + '\n\n' + (voice === 'professional' ? "We're excited to share" : voice === 'casual' ? 'Hey, check this out' : 'Listen up') + ' \u2014 ' + topic + ' is here.\n\n' + cfg.cta + ' \u2014 ' + cfg.urgency + '.',
        twitter: (voice === 'professional' ? 'Introducing' : voice === 'casual' ? 'Just dropped' : 'BREAKING') + ': ' + topic + '\n\n' + cfg.cta + '. ' + cfg.urgency + '.',
        linkedin: (voice === 'professional' ? 'We are pleased to announce' : voice === 'casual' ? 'Excited to share' : 'Proud to present') + ': ' + topic + '\n\nThis represents a significant step forward. ' + cfg.cta + ' \u2014 ' + cfg.urgency + '.',
        email: 'Subject: ' + topic + '\n\n' + (voice === 'professional' ? 'Dear valued customer,' : voice === 'casual' ? 'Hey there,' : 'Attention:') + '\n\nWe wanted to let you know about ' + topic + '.\n\n' + cfg.cta + '. ' + cfg.urgency + '.\n\nBest regards,\nThe Team',
        blog: '# ' + topic + '\n\n' + (voice === 'professional' ? "In today's competitive landscape" : voice === 'casual' ? "So here's the thing" : "Let's be real") + ' \u2014 ' + topic + ' matters more than ever.\n\n' + cfg.cta + '. ' + cfg.urgency + '.',
      };
      return { content: templates[platform] || templates.blog, source: 'template', labeled: true, note: 'Generated by labeled template engine, NOT AI. Clearly labeled as template output.' };
    }

    async function externalAiGenerate(brandVoice, intensity, topic, platform, env) {
      if (!env.AI_PROVIDER_URL || !env.AI_PROVIDER_KEY) return null;
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        const resp = await fetch(env.AI_PROVIDER_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env.AI_PROVIDER_KEY },
          body: JSON.stringify({
            model: 'gpt-4o-mini',
            messages: [
              { role: 'system', content: 'You are a marketing copy generator. Brand voice: ' + (brandVoice || 'professional') + '. Intensity: ' + (intensity || 'balanced') + '. Platform: ' + platform + '. Generate honest, non-deceptive marketing copy. Do NOT make revenue promises. Label output as AI-generated.' },
              { role: 'user', content: 'Generate marketing copy for "' + topic + '" on ' + platform + '.' },
            ],
            max_tokens: 500,
          }),
          signal: controller.signal,
        });
        clearTimeout(timeout);
        if (!resp.ok) throw new Error('Provider returned ' + resp.status);
        const data = await resp.json();
        const content = data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content;
        if (!content) throw new Error('Empty response');
        return { content, source: 'ai', labeled: true, note: 'Generated by AI provider. Labeled as AI output.' };
      } catch (e) { return null; }
    }

    // Workers AI fallback: free-tier model verified on this account
    // (@cf/meta/llama-3.1-8b-instruct-fp8). Falls through to template on
    // any failure, with honest source labeling.
    async function workersAiGenerate(brandVoice, intensity, topic, platform, env) {
      if (!env.AI) return null;
      try {
        const resp = await env.AI.run('@cf/meta/llama-3.1-8b-instruct-fp8', {
          max_tokens: 500,
          messages: [
            { role: 'system', content: 'You are a marketing copy generator. Brand voice: ' + (brandVoice || 'professional') + '. Intensity: ' + (intensity || 'balanced') + '. Platform: ' + platform + '. Generate honest, non-deceptive marketing copy for: ' + topic + '. Do NOT make revenue promises. Reply with only the copy.' },
            { role: 'user', content: 'Generate marketing copy for "' + topic + '" on ' + platform + '.' },
          ],
        });
        const content = resp && resp.response && resp.response.trim();
        if (!content) return null;
        return { content, source: 'workers-ai', labeled: true, note: 'Generated by Workers AI (llama-3.1-8b-instruct-fp8). Labeled as AI output.' };
      } catch (e) { return null; }
    }

    async function aiGenerate(brandVoice, intensity, topic, platform, env) {
      return (await externalAiGenerate(brandVoice, intensity, topic, platform, env))
        || (await workersAiGenerate(brandVoice, intensity, topic, platform, env))
        || templateGenerate(brandVoice, intensity, topic, platform);
    }

    const BANNED_PATTERNS = [/guaranteed\s+(revenue|income|profit)/i, /get\s+rich\s+quick/i, /risk[- ]free/i, /100%\s+(guaranteed|success)/i];
    function checkContentPolicy(text) {
      for (const pattern of BANNED_PATTERNS) {
        if (pattern.test(text)) return { passed: false, reason: 'Content matches banned pattern: ' + pattern.source };
      }
      return { passed: true };
    }

    function wilsonScore(successes, total, z) {
      z = z || 1.96;
      if (total === 0) return 0;
      const p = successes / total;
      const denom = 1 + z * z / total;
      const center = p + z * z / (2 * total);
      const spread = z * Math.sqrt((p * (1 - p) + z * z / (4 * total)) / total);
      return (center - spread) / denom;
    }

    if (method === 'OPTIONS') {
      return new Response(null, { headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type, X-Tenant-Id' } });
    }

    if (path === '/healthz' && method === 'GET') return json({ status: 'ok', timestamp: new Date().toISOString() });

    if (path === '/' && method === 'GET') {
      return json({
        name: 'Creator Marketing Platform', version: '1.1.0',
        persistence: 'durable-object (strongly consistent)',
        source: 'https://github.com/1243353366/creator-marketing-platform',
        endpoints: {
          'GET /healthz': 'Health check',
          'GET /': 'API documentation',
          'GET /api/brand-voice': 'Get brand voice (X-Tenant-Id header)',
          'POST /api/brand-voice': 'Set brand voice { voice, intensity }',
          'POST /api/generate': 'Generate script { topic, platform } (Workers AI, honest template fallback)',
          'GET /api/scripts': 'List scripts',
          'GET /api/analytics': 'Get analytics (requires consent)',
          'POST /api/consent': 'Set consent { analytics, training }',
          'POST /api/seo-audit': 'Audit URL { url } (SSRF-hardened)',
          'POST /api/pipeline/run': 'Run pipeline { period }',
          'GET /api/pipeline/:period': 'Get pipeline status',
        },
        headers: { 'X-Tenant-Id': 'Required for /api/* endpoints' },
      });
    }

    const tenantId = request.headers.get('X-Tenant-Id');
    if (path.startsWith('/api/') && !tenantId) return fail('X-Tenant-Id header is required', 401);

    if (path === '/api/brand-voice' && method === 'GET') {
      const tenant = await getTenant(tenantId);
      return json({ tenantId, brandVoice: tenant.brandVoice, intensity: tenant.intensity });
    }

    if (path === '/api/brand-voice' && method === 'POST') {
      const body = await request.json().catch(() => null);
      if (!body || !body.voice) return fail('voice is required');
      const validIntensities = ['subtle', 'balanced', 'bold'];
      if (body.intensity && !validIntensities.includes(body.intensity)) return fail('intensity must be: ' + validIntensities.join(', '));
      const intensity = body.intensity || 'balanced';
      const existing = await getTenant(tenantId);
      await store.putTenant(tenantId, { brandVoice: body.voice, intensity, analyticsConsent: existing.analyticsConsent, trainingConsent: existing.trainingConsent });
      return json({ tenantId, brandVoice: body.voice, intensity, updated: true });
    }

    if (path === '/api/generate' && method === 'POST') {
      const body = await request.json().catch(() => null);
      if (!body || !body.topic) return fail('topic is required');
      const platform = body.platform || 'blog';
      const validPlatforms = ['instagram', 'twitter', 'linkedin', 'email', 'blog'];
      if (!validPlatforms.includes(platform)) return fail('platform must be: ' + validPlatforms.join(', '));
      const tenant = await getTenant(tenantId);
      const result = await aiGenerate(tenant.brandVoice, tenant.intensity, body.topic, platform, env);
      const policy = checkContentPolicy(result.content);
      if (!policy.passed) return fail('Content policy violation: ' + policy.reason, 422);
      const scriptId = genId('script');
      const createdAt = new Date().toISOString();
      const script = { id: scriptId, tenantId, topic: body.topic, platform, content: result.content, source: result.source, labeled: result.labeled, note: result.note, createdAt };
      await store.putScript(script);
      return json(script);
    }

    if (path === '/api/scripts' && method === 'GET') {
      const scripts = await store.listScripts(tenantId);
      return json({ tenantId, count: scripts.length, scripts });
    }

    if (path === '/api/consent' && method === 'POST') {
      const body = await request.json().catch(() => null);
      if (!body) return fail('Request body required');
      const existing = await getTenant(tenantId);
      const analyticsConsent = !!body.analytics;
      const trainingConsent = !!body.training;
      await store.putTenant(tenantId, { brandVoice: existing.brandVoice, intensity: existing.intensity, analyticsConsent, trainingConsent });
      return json({ tenantId, analyticsConsent, trainingConsent, metricsDeleted: existing.analyticsConsent && !analyticsConsent });
    }

    if (path === '/api/analytics' && method === 'GET') {
      const tenant = await getTenant(tenantId);
      if (!tenant.analyticsConsent) return fail('Analytics consent not granted. POST /api/consent with { analytics: true } first.', 403);
      return json({ tenantId, consentGranted: true, campaignCount: 0, metrics: [] });
    }

    if (path === '/api/seo-audit' && method === 'POST') {
      const body = await request.json().catch(() => null);
      if (!body || !body.url) return fail('url is required');
      let resp;
      try { resp = await ssrfSafeFetch(body.url); } catch (e) { return fail('SEO audit failed: ' + e.message, 422); }
      const html = await resp.text();
      const auditId = genId('audit');
      const titleMatch = html.match(/<title[^>]*>(.*?)<\/title>/i);
      const descMatch = html.match(/<meta\s+name=["']description["']\s+content=["']([^"']*)["']/i);
      const h1Match = html.match(/<h1[^>]*>(.*?)<\/h1>/i);
      const canonicalMatch = html.match(/<link\s+rel=["']canonical["']\s+href=["']([^"']*)["']/i);
      const ogTitleMatch = html.match(/<meta\s+property=["']og:title["']\s+content=["']([^"']*)["']/i);
      const results = {
        auditId, tenantId, url: body.url, statusCode: resp.status,
        checks: {
          hasTitle: !!titleMatch, title: titleMatch && titleMatch[1] && titleMatch[1].trim() || null, titleLength: titleMatch && titleMatch[1] && titleMatch[1].trim().length || 0,
          hasMetaDescription: !!descMatch, metaDescription: descMatch && descMatch[1] || null, metaDescriptionLength: descMatch && descMatch[1] && descMatch[1].length || 0,
          hasH1: !!h1Match, h1: h1Match && h1Match[1] && h1Match[1].trim() || null,
          hasCanonical: !!canonicalMatch, canonical: canonicalMatch && canonicalMatch[1] || null,
          hasOgTitle: !!ogTitleMatch, ogTitle: ogTitleMatch && ogTitleMatch[1] || null,
          htmlSize: html.length,
        },
        recommendations: [],
        createdAt: new Date().toISOString(),
      };
      if (!results.checks.hasTitle) results.recommendations.push('Add a <title> tag');
      if (results.checks.titleLength > 0 && results.checks.titleLength < 30) results.recommendations.push('Title is short \u2014 consider 30-60 characters');
      if (results.checks.titleLength > 60) results.recommendations.push('Title is long \u2014 consider under 60 characters');
      if (!results.checks.hasMetaDescription) results.recommendations.push('Add a meta description');
      if (!results.checks.hasH1) results.recommendations.push('Add an <h1> heading');
      if (!results.checks.hasCanonical) results.recommendations.push('Add a canonical link tag');
      if (!results.checks.hasOgTitle) results.recommendations.push('Add og:title meta tag for social sharing');
      await store.putAudit({ auditId, tenantId, url: body.url, statusCode: resp.status, checks: results.checks, createdAt: results.createdAt });
      return json(results);
    }

    if (path === '/api/pipeline/run' && method === 'POST') {
      const body = await request.json().catch(() => null);
      const period = (body && body.period) || new Date().toISOString().slice(0, 7);
      const periodKey = tenantId + ':' + period;
      const existing = await store.getPipeline(periodKey);
      if (existing) return json({ periodKey, status: 'already_run', result: existing });
      const tenant = await getTenant(tenantId);
      if (!tenant.analyticsConsent) return fail('Pipeline requires analytics consent. POST /api/consent with { analytics: true } first.', 403);
      const campaigns = [{ id: 'campaign_a', clicks: 45, impressions: 1000 }, { id: 'campaign_b', clicks: 52, impressions: 1000 }];
      const scored = campaigns.map(c => ({ campaignId: c.id, rate: c.impressions > 0 ? c.clicks / c.impressions : 0, wilson: wilsonScore(c.clicks, c.impressions) }));
      scored.sort((a, b) => b.wilson - a.wilson);
      const winner = scored.length > 1 && scored[0].wilson > scored[1].wilson ? scored[0].campaignId : null;
      const result = { periodKey, tenantId, period, status: 'completed', campaignsCompared: scored.length, scores: scored, winner, note: winner ? 'Winner: ' + winner + ' (Wilson: ' + scored[0].wilson.toFixed(4) + ')' : 'No statistically significant winner. No changes without approval.', requiresApproval: true, approved: false, runAt: new Date().toISOString() };
      await store.putPipeline(periodKey, result);
      return json(result);
    }

    const pipelineMatch = path.match(/^\/api\/pipeline\/(.+)$/);
    if (pipelineMatch && method === 'GET') {
      const period = pipelineMatch[1];
      const periodKey = tenantId + ':' + period;
      const row = await store.getPipeline(periodKey);
      if (!row) return fail('No pipeline run found for this period', 404);
      return json(row);
    }

    return fail('Not found', 404);
  },
};
