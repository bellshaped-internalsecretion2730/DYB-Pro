import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import AgentSwarm, { agentState } from "@/components/AgentSwarm";
import AskPane from "@/components/AskPane";
import FoldStrip from "@/components/FoldStrip";
import IconRail, { type RailSection } from "@/components/IconRail";
import ProteinViewer, { residueIndex } from "@/components/ProteinViewer";
import { agents, cycle, nodes, project } from "./fixtures";

describe("left zone — ask & agent control", () => {
  const renderPane = (section: RailSection = "ask") =>
    render(
      <AskPane
        section={section}
        selectedLabel="GB1-v12"
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
        onSelectProject={vi.fn()}
        onBriefChange={vi.fn()}
        onRun={vi.fn()}
        onCancel={vi.fn()}
        onSeedDemo={vi.fn()}
        onUpload={vi.fn()}
        onAttachCycle={vi.fn()}
      />,
    );

  it("keeps the inline prompt and run controls in the ask section", () => {
    renderPane();
    expect(screen.getByLabelText("research brief")).toHaveValue("Improve GB1 thermal stability");
    expect(screen.getByRole("button", { name: "cycle running…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "load demo project" })).toBeEnabled();
    expect(screen.getByLabelText("project")).toHaveValue(project.id);
    expect(screen.queryByLabelText("upload sequences or structures")).toBeNull();
  });

  it("keeps uploads reachable from the files section", () => {
    renderPane("files");
    expect(screen.getByLabelText("upload sequences or structures")).toBeInTheDocument();
  });

  it("renders the plan and offers no run control the API lacks", () => {
    renderPane("agents");
    expect(screen.getByText("sequence")).toBeInTheDocument();
    expect(screen.getByText(/round 3 · awaiting_agents/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "cancel run" })).toBeEnabled();
    ["pause", "resume", "redirect", "spawn"].forEach((c) => {
      expect(screen.queryByRole("button", { name: c })).toBeNull();
    });
  });

  it("exposes history and a hand-off in their own sections", () => {
    renderPane("history");
    expect(screen.getByText(/cycle r3/)).toBeInTheDocument();
    renderPane("handoff");
    expect(screen.getByText("from GB1-v12")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "hand off current brief" })).toBeDisabled();
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
    expect(onSelect).toHaveBeenLastCalledWith("handoff");
    fireEvent.keyDown(screen.getByTestId("icon-rail"), { key: "End" });
    expect(onSelect).toHaveBeenLastCalledWith("handoff");
  });
});

describe("right zone — pixel agent swarm", () => {
  it("groups agents by role and shows real statuses", () => {
    render(<AgentSwarm cycle={cycle} agents={agents} />);
    expect(screen.getByTestId("agent-swarm")).toBeInTheDocument();
    expect(screen.getAllByTestId("agent-card")).toHaveLength(4);
    expect(screen.getByText("orchestrator", { selector: ".label" })).toBeInTheDocument();
    expect(screen.getByText("wetlab planner")).toBeInTheDocument();
    expect(screen.getByText("1 active · 4 runs")).toBeInTheDocument();
    expect(screen.getByText("toolkit rejected proposal")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /devin session/ })).toHaveAttribute(
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
    expect(agentState("running")).toMatchObject({ label: "thinking", active: true });
    expect(agentState("finished")).toMatchObject({ label: "done", active: false });
    expect(agentState("pending")).toMatchObject({ label: "queued", active: false });
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
    expect(screen.getByText(/composite 0.842/)).toBeInTheDocument();
    ["zoom in", "zoom out", "reset", "labels", "exit compare"].forEach((c) =>
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
