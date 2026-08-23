import { Container, getContainer } from "@cloudflare/containers";
import type { StopParams } from "@cloudflare/containers";

const CONTAINER_INSTANCE = "primary";

function containerEnvironment(env: CloudflareEnv): Record<string, string> {
  return {
    APP_ENV: env.APP_ENV,
    LOG_LEVEL: env.LOG_LEVEL,
    DATABASE_URL: env.DATABASE_URL,
    REDIS_URL: env.REDIS_URL,
    CELERY_TASK_ALWAYS_EAGER: env.CELERY_TASK_ALWAYS_EAGER,
    CORS_ORIGINS: env.CORS_ORIGINS,
    ALLOW_LOCAL_SIMULATION: env.ALLOW_LOCAL_SIMULATION,
    DEVIN_API_KEY: env.DEVIN_API_KEY,
    DEVIN_ORG_ID: env.DEVIN_ORG_ID,
    DEVIN_API_BASE: env.DEVIN_API_BASE,
    DEVIN_API_FLAVOR: env.DEVIN_API_FLAVOR,
    DEVIN_APP_BASE: env.DEVIN_APP_BASE,
    DEVIN_CHILD_REPO: env.DEVIN_CHILD_REPO,
    DEVIN_ORCHESTRATOR_ACU_LIMIT: env.DEVIN_ORCHESTRATOR_ACU_LIMIT,
    DEVIN_CHILD_ACU_LIMIT: env.DEVIN_CHILD_ACU_LIMIT,
    DEVIN_POLL_INTERVAL_SECONDS: env.DEVIN_POLL_INTERVAL_SECONDS,
    DEVIN_SESSION_TIMEOUT_SECONDS: env.DEVIN_SESSION_TIMEOUT_SECONDS,
    DEVIN_REQUEST_TIMEOUT_SECONDS: env.DEVIN_REQUEST_TIMEOUT_SECONDS,
    AGENT_MAX_ATTEMPTS: env.AGENT_MAX_ATTEMPTS,
    OPENAI_API_KEY: env.OPENAI_API_KEY,
    OPENAI_MODEL: env.OPENAI_MODEL,
    OPENAI_VISION_MODEL: env.OPENAI_VISION_MODEL,
    ALPHAFOLD_API_URL: env.ALPHAFOLD_API_URL,
    ALPHAFOLD_API_KEY: env.ALPHAFOLD_API_KEY,
    ALPHAFOLD_MODEL_VERSION: env.ALPHAFOLD_MODEL_VERSION,
    ALPHAFOLD_TIMEOUT_SECONDS: env.ALPHAFOLD_TIMEOUT_SECONDS,
    PROTEINMPNN_API_URL: env.PROTEINMPNN_API_URL,
    PROTEINMPNN_API_KEY: env.PROTEINMPNN_API_KEY,
    PROTEINMPNN_MODEL_VERSION: env.PROTEINMPNN_MODEL_VERSION,
    PROTEINMPNN_TIMEOUT_SECONDS: env.PROTEINMPNN_TIMEOUT_SECONDS,
    PROTEINMPNN_NUM_SEQUENCES: env.PROTEINMPNN_NUM_SEQUENCES,
    PROTEINMPNN_SAMPLING_TEMPERATURE: env.PROTEINMPNN_SAMPLING_TEMPERATURE,
    PROTEINMPNN_RANDOM_SEED: env.PROTEINMPNN_RANDOM_SEED,
    RESEARCH_DAEMON_ENABLED: env.RESEARCH_DAEMON_ENABLED,
    RESEARCH_DEBOUNCE_SECONDS: env.RESEARCH_DEBOUNCE_SECONDS,
    RESEARCH_TICK_SECONDS: env.RESEARCH_TICK_SECONDS,
    RESEARCH_ACU_LIMIT: env.RESEARCH_ACU_LIMIT,
    RESEARCH_MAX_TOPICS_PER_EVENT: env.RESEARCH_MAX_TOPICS_PER_EVENT,
    DAEMON_ENABLED: env.DAEMON_ENABLED,
    DAEMON_TICK_SECONDS: env.DAEMON_TICK_SECONDS,
    DAEMON_DEBOUNCE_SECONDS: env.DAEMON_DEBOUNCE_SECONDS,
    DAEMON_MAX_ATTEMPTS: env.DAEMON_MAX_ATTEMPTS,
    DAEMON_CHILD_ACU_LIMIT: env.DAEMON_CHILD_ACU_LIMIT,
    DAEMON_CHILD_TIMEOUT_SECONDS: env.DAEMON_CHILD_TIMEOUT_SECONDS,
    DAEMON_LITERATURE_NETWORK: env.DAEMON_LITERATURE_NETWORK,
    PUBLIC_API_BASE: env.PUBLIC_API_BASE,
    PHARMA_DAEMON_FREQUENCY: env.PHARMA_DAEMON_FREQUENCY,
    S3_ENDPOINT_URL: env.S3_ENDPOINT_URL,
    S3_ACCESS_KEY: env.S3_ACCESS_KEY,
    S3_SECRET_KEY: env.S3_SECRET_KEY,
    S3_BUCKET: env.S3_BUCKET,
    S3_REGION: env.S3_REGION,
    LOCAL_ARTIFACT_DIR: env.LOCAL_ARTIFACT_DIR,
    SEED_ADMIN_API_KEY: env.SEED_ADMIN_API_KEY,
    SEED_SCIENTIST_API_KEY: env.SEED_SCIENTIST_API_KEY,
    SEED_VIEWER_API_KEY: env.SEED_VIEWER_API_KEY,
    DEFAULT_ACU_QUOTA: env.DEFAULT_ACU_QUOTA,
    DEFAULT_CYCLE_QUOTA: env.DEFAULT_CYCLE_QUOTA,
  };
}

export class DybProContainer extends Container<CloudflareEnv> {
  defaultPort = 8000;
  requiredPorts = [8000];
  sleepAfter = "10m";
  envVars = containerEnvironment(this.env);
  enableInternet = true;
  pingEndpoint = "localhost/api/healthz";

  override onStart(): void {
    console.log(JSON.stringify({ event: "container_started", instance: CONTAINER_INSTANCE }));
  }

  override onStop(params: StopParams): void {
    console.log(
      JSON.stringify({
        event: "container_stopped",
        instance: CONTAINER_INSTANCE,
        exitCode: params.exitCode,
        reason: params.reason,
      }),
    );
  }

  override onError(error: unknown): never {
    const message = error instanceof Error ? error.message : String(error);
    console.error(
      JSON.stringify({ event: "container_error", instance: CONTAINER_INSTANCE, error: message }),
    );
    throw error;
  }

  override async onActivityExpired(): Promise<void> {
    // Celery workers and beat perform autonomous work after the initiating HTTP request ends.
    // Keeping the singleton awake is deliberate; managed Redis preserves queued work if the
    // platform still restarts the container.
    console.log(JSON.stringify({ event: "container_keepalive", instance: CONTAINER_INSTANCE }));
    this.renewActivityTimeout();
  }
}

export default {
  async fetch(request: Request, env: CloudflareEnv): Promise<Response> {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) {
      return Response.json({ detail: "not found" }, { status: 404 });
    }

    console.log(
      JSON.stringify({ event: "api_proxy", method: request.method, path: url.pathname }),
    );
    try {
      return await getContainer(env.DYB_PRO_CONTAINER, CONTAINER_INSTANCE).fetch(request);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.error(
        JSON.stringify({ event: "api_proxy_error", path: url.pathname, error: message }),
      );
      return Response.json(
        { detail: "the DYB Pro compute service is unavailable" },
        { status: 503 },
      );
    }
  },
} satisfies ExportedHandler<CloudflareEnv>;
