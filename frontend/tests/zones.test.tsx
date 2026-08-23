import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgentSwarm, { agentState } from "@/components/AgentSwarm";
import AskPane from "@/components/AskPane";
import FoldStrip from "@/components/FoldStrip";
import IconRail, { type RailSection } from "@/components/IconRail";
import ProteinViewer, { residueIndex } from "@/components/ProteinViewer";
import { api } from "@/lib/api";
import { agents, cycle, nodes, project } from "./fixtures";

beforeEach(() => {
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/assistant/models") {
      return { default: "gpt-5.6-terra", models: ["gpt-5.6-terra"] } as never;
    }
    return [] as never;
  });
});

afterEach(() => vi.restoreAllMocks());

describe("left zone — ask & agent control", () => {
  const renderPane = (section: RailSection = "ask") =>
    render(
      <AskPane
        section={section}
        selectedLabel="GB1-v12"
        handoffNote={null}
        onHandoff={vi.fn()}
        projects={[project]}
        project={project}
        cycles={[cycle]}
        cycle={cycle}
        agents={agents}
        brief="Improve GB1 thermal stability"
        busy={null}
        running
        fileRef={createRef<HTMLInputElement>()}
        selected={nodes[0]}
        onSelectProject={vi.fn()}
        onBriefChange={vi.fn()}
        onRun={vi.fn()}
        onCancel={vi.fn()}
        onSeedDemo={vi.fn()}
        onUpload={vi.fn()}
        onAttachCycle={vi.fn()}
        onOpenTab={vi.fn()}
        onResearch={vi.fn()}
        onSelectVersion={vi.fn(() => true)}
        onAdvanceProgram={vi.fn()}
      />,
    );

  it("keeps a context-aware chat and compact run controls in the ask section", async () => {
    renderPane();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/assistant/models"));
    expect(screen.getByLabelText("Ask the workspace agent")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Demo" })).toBeEnabled();
    expect(screen.getByLabelText("project")).toHaveValue(project.id);
    expect(screen.queryByLabelText("upload sequences or structures")).toBeNull();
    expect(screen.getByText("GB1-v12")).toBeInTheDocument();
  });

  it("keeps uploads reachable from the files section", () => {
    renderPane("files");
    expect(screen.getByLabelText("upload sequences or structures")).toBeInTheDocument();
  });

  it("renders the plan and offers no run control the API lacks", () => {
    renderPane("agents");
    expect(screen.getByText("sequence")).toBeInTheDocument();
    expect(screen.getByText(/Round 3 · awaiting_agents/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeEnabled();
    // no placeholder run controls: every button in the pane is wired to a live handler
    ["Pause", "Resume", "Redirect", "Spawn"].forEach((c) => {
      expect(screen.queryByRole("button", { name: c })).toBeNull();
    });
  });

  it("exposes history and a hand-off in their own sections", () => {
    renderPane("history");
    expect(screen.getByText(/Cycle r3/)).toBeInTheDocument();
    renderPane("handoff");
    expect(screen.getByText("From GB1-v12")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hand off current brief" })).toBeDisabled();
  });

  it("moves the compact drug program and binding inputs into the rail", async () => {
    renderPane("pharma");
    await waitFor(() => expect(api.get).toHaveBeenCalledWith(`/projects/${project.id}/programs`));
    expect(screen.getByTestId("pharma-pane")).toBeInTheDocument();
    expect(screen.getByLabelText("target PDB")).toBeInTheDocument();
    expect(screen.getByLabelText("ligand PDB")).toBeInTheDocument();
  });
});

describe("left icon rail", () => {
  it("switches sections by click and arrow keys", () => {
    const onSelect = vi.fn();
    const { rerender } = render(<IconRail active="ask" onSelect={onSelect} />);
    const agents = screen.getByTestId("rail-agents");
    expect(agents).toHaveAttribute("aria-label", "Agent control and playbook");
    agents.click();
    expect(onSelect).toHaveBeenCalledWith("agents");

    rerender(<IconRail active="ask" onSelect={onSelect} />);
    fireEvent.keyDown(screen.getByTestId("icon-rail"), { key: "ArrowUp" });
    expect(onSelect).toHaveBeenLastCalledWith("pharma");
    fireEvent.keyDown(screen.getByTestId("icon-rail"), { key: "End" });
    expect(onSelect).toHaveBeenLastCalledWith("pharma");
  });
});

describe("right zone — pixel agent swarm", () => {
  it("groups agents by role and shows real statuses", () => {
    render(<AgentSwarm cycle={cycle} agents={agents} />);
    expect(screen.getByTestId("agent-swarm")).toBeInTheDocument();
    expect(screen.getAllByTestId("agent-card")).toHaveLength(4);
    expect(screen.getByText("Orchestrator", { selector: ".label" })).toBeInTheDocument();
    expect(screen.getByText("Wetlab planner")).toBeInTheDocument();
    expect(screen.getByText("1 active · 4 runs")).toBeInTheDocument();
    expect(screen.getByText("toolkit rejected proposal")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Devin session/ })).toHaveAttribute(
      "href",
      "https://app.devin.ai/sessions/lit",
    );
  });

  it("styles agent-generated feeds and the plan as glass", () => {
    render(<AgentSwarm cycle={cycle} agents={agents} />);
    expect(screen.getByTestId("plan-strategy").className).toContain("glass");
    screen.getAllByTestId("agent-feed").forEach((el) => expect(el.className).toContain("glass"));
    expect(screen.getByText("fanned out 4 children")).toBeInTheDocument();
  });

  it("maps every backend status to a state without inventing activity", () => {
    expect(agentState("running")).toMatchObject({ label: "Thinking", active: true });
    expect(agentState("finished")).toMatchObject({ label: "Done", active: false });
    expect(agentState("pending")).toMatchObject({ label: "Queued", active: false });
    expect(agentState("failed").active).toBe(false);
  });

  it("does not render sprites without a cycle", () => {
    render(<AgentSwarm cycle={null} agents={[]} />);
    expect(screen.queryAllByTestId("agent-card")).toHaveLength(0);
  });
});

describe("center zone — protein viewer and fold strip", () => {
  it("renders the selected commit, sequence track and camera controls", () => {
    render(
      <ProteinViewer
        node={nodes[0]}
        compareNode={nodes[1]}
        sequence="MTYKLILNG"
        onClearCompare={vi.fn()}
      />,
    );
    expect(screen.getByText("GB1-v12")).toBeInTheDocument();
    expect(screen.getByText("a1b2c3d")).toBeInTheDocument();
    expect(screen.getByText(/vs GB1-v8/)).toBeInTheDocument();
    expect(screen.getByText("T2I")).toBeInTheDocument();
    expect(screen.getByTestId("sequence-track").textContent).toBe("MTYKLILNG");
    expect(screen.getByText(/Composite 0.842/)).toBeInTheDocument();
    ["Zoom in", "Zoom out", "Reset", "Labels", "Exit compare"].forEach((c) =>
      expect(screen.getByRole("button", { name: c })).toBeInTheDocument(),
    );
  });

  it("guides an empty viewer instead of faking a structure", () => {
    render(<ProteinViewer node={null} compareNode={null} sequence={null} onClearCompare={vi.fn()} />);
    expect(screen.getByText(/Run a design cycle or pick a version below/)).toBeInTheDocument();
    expect(screen.queryByTestId("sequence-track")).toBeNull();
  });

  it("lists every version with metrics and click-to-load", () => {
    const onSelect = vi.fn();
    render(
      <FoldStrip
        nodes={nodes}
        selectedId="commit-12"
        compareId="commit-8"
        onSelect={onSelect}
        onCompare={vi.fn()}
      />,
    );
    expect(screen.getByTestId("fold-strip")).toBeInTheDocument();
    expect(screen.getByText(/2 commits · click to load/)).toBeInTheDocument();
    const head = screen.getByRole("button", { name: "version GB1-v12" });
    expect(head).toHaveAttribute("aria-current", "true");
    head.click();
    expect(onSelect).toHaveBeenCalledWith(nodes[0]);
    expect(screen.getByText("0.842 · head")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "version GB1-v8" }).className).toContain("compare");
  });

  it("parses residue positions from mutation labels", () => {
    expect(residueIndex("T2I")).toBe(2);
    expect(residueIndex("K10R")).toBe(10);
    expect(residueIndex("root")).toBeNull();
  });
});
