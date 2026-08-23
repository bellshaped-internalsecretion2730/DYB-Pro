"use client";

import type { Provider } from "@/lib/api";

export default function ProviderBadge({ provider }: { provider: Provider | null }) {
  if (!provider) return <span className="badge">Provider…</span>;
  if (provider.provider === "devin") {
    const reachable = provider.devin_reachable !== false;
    return (
      <span
        className={reachable ? "badge devin" : "badge bad"}
        title={
          reachable
            ? "Real Devin organization agents are connected."
            : provider.error ?? "Check the server-side Devin credentials."
        }
      >
        {reachable
          ? `Devin ${provider.devin_api_flavor}`
          : "Devin offline"}
      </span>
    );
  }
  if (provider.provider === "local-simulation") {
    return (
      <span
        className="badge sim"
        title="No Devin credentials are configured. Runs use the honest local simulation provider."
      >
        Local
      </span>
    );
  }
  return <span className="badge bad">{provider.error || "No provider available"}</span>;
}
