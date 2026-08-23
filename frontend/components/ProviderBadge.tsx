"use client";

import type { Provider } from "@/lib/api";

export default function ProviderBadge({ provider }: { provider: Provider | null }) {
  if (!provider) return <span className="badge">Provider…</span>;
  if (provider.provider === "devin") {
    const reachable = provider.devin_reachable !== false;
    return (
      <span className={reachable ? "badge devin" : "badge bad"}>
        {reachable
          ? `Real Devin agents · ${provider.devin_api_flavor}`
          : `Devin unreachable · ${provider.error ?? "check key"}`}
      </span>
    );
  }
  if (provider.provider === "local-simulation") {
    return (
      <span className="badge sim" title="No Devin credentials configured — results are labelled local-simulation everywhere.">
        local-simulation (no Devin key)
      </span>
    );
  }
  return <span className="badge bad">{provider.error || "No provider available"}</span>;
}
