import { Container } from "@cloudflare/containers";

export interface Env {
  APP: DurableObjectNamespace;
  SECRET_KEY?: string;
  FLASK_ENV?: string;
}

// Single named instance: the app's embedded PostgreSQL lives on the
// instance disk, so there must be exactly one serving instance.
export class AppContainer extends Container {
  defaultPort = 8000;
  sleepAfter = "5m";
  override envVars = {
    SECRET_KEY: (this.env as any)?.SECRET_KEY ?? "",
    FLASK_ENV: (this.env as any)?.FLASK_ENV ?? "production",
  };
  override onStart() {
    console.log("app container started");
  }
  override onError(error: unknown) {
    console.log("app container error:", error);
  }
}

function json(body: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2) + "\n", {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function failureBody(request: Request, state: string, detail: string, retryAfter: number): Record<string, unknown> {
  return {
    ok: false,
    state,
    detail,
    request_path: new URL(request.url).pathname,
    retryable: true,
    retry_after_seconds: retryAfter,
    hint:
      state === "container_warming"
        ? "The container instance is booting (cold start or first-deploy provisioning). Retry shortly."
        : "The app container is not reachable. Check Containers logs in the Cloudflare dashboard.",
    timestamp: new Date().toISOString(),
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Worker-side status probe: answers even when the container is down,
    // so the first-deploy provisioning window is observable.
    if (url.pathname === "/status") {
      const started = Date.now();
      let app = "ok";
      let detail = "";
      try {
        const probe = await env.APP.getByName("app").fetch("http://container/healthz");
        app = probe.ok ? "ok" : `degraded (healthz ${probe.status})`;
      } catch (e) {
        app = "unreachable";
        detail = String((e as Error)?.message ?? e).slice(0, 300);
      }
      return json({
        ok: app === "ok",
        worker: "ok",
        app,
        detail,
        probe_ms: Date.now() - started,
        timestamp: new Date().toISOString(),
      });
    }

    try {
      return await env.APP.getByName("app").fetch(request);
    } catch (e) {
      const msg = String((e as Error)?.message ?? e).slice(0, 300);
      const warming = /timed? ?out|cold|starting|not yet|sleep|provision/i.test(msg);
      return json(failureBody(request, warming ? "container_warming" : "container_unavailable", msg, warming ? 10 : 60), 503);
    }
  },
};
