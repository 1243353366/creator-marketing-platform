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

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const container = env.APP.getByName("app");
    return container.fetch(request);
  },
};
