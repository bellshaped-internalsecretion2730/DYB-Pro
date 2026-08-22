import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import LearnedPane from "@/components/LearnedPane";
import type { Learned, Proposal } from "@/lib/api";

const learned: Learned = {
  headline: "v1 → v3: drift shrinking on Tm",
  lessons: ["oxidising host raised soluble fraction"],
  drift: [],
  campaign_id: "rp-1",
  versions: [],
  paper_count: 0,
  hypotheses: { supported: 0, refuted: 0, open: 0 },
  generated_at: "2026-01-01T00:00:00Z",
};

describe("learned pane", () => {
  it("explains a blocked proposal instead of crashing", () => {
    const proposal: Proposal = {
      proposed: null,
      target_metric: "melting_temperature",
      reason: "every derived candidate is either protected by a label or already refuted",
    };
    render(
      <LearnedPane learned={learned} proposal={proposal} busy={null} onProposal={vi.fn()} />,
    );
    expect(screen.getByTestId("proposal-blocked").textContent).toContain("protected by a label");
    expect(screen.queryByText(/predicted wet-lab/)).toBeNull();
  });

  it("renders the proposed version when there is one", () => {
    const proposal: Proposal = {
      proposed: {
        label: "v4-candidate",
        mutations: ["K12E"],
        sequence: "MTY",
        rationale: "charge pair stabilises the helix",
        scores: {},
        predicted_wetlab: {
          melting_temperature: {
            value: 62.1,
            sd: 1.8,
            unit: "C",
            method: "ddG proxy",
            skill: "skill.physics",
          },
        },
        predicted_delta: { melting_temperature: 2.4 },
        citations: [],
      },
      target_metric: "melting_temperature",
      why_this_metric: "largest residual",
      skills_used: ["skill.physics"],
    };
    render(
      <LearnedPane learned={learned} proposal={proposal} busy={null} onProposal={vi.fn()} />,
    );
    expect(screen.getByText(/v4-candidate/)).toBeTruthy();
    expect(screen.queryByTestId("proposal-blocked")).toBeNull();
  });
});
