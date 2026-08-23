"use client";

import { useEffect, useRef, useState } from "react";
import {
  api,
  type AssistantAction,
  type AssistantChatResponse,
  type BindingInput,
  type Cycle,
  type GraphNode,
  type Project,
} from "@/lib/api";

export type WorkspaceTab = "structure" | "lineage" | "shortlist" | "research" | "lab" | "data";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  model?: string;
  actions?: AssistantAction[];
};

const SKILLS = [
  ["workflow", "Workflow"],
  ["structure", "Structure"],
  ["research", "Research"],
  ["drug-discovery", "Drug discovery"],
] as const;

function messageId() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

export default function WorkspaceChat({
  project,
  selected,
  cycle,
  onRun,
  onHandoff,
  onOpenTab,
  onResearch,
  onSelectVersion,
  onAdvanceProgram,
}: {
  project: Project | null;
  selected: GraphNode | null;
  cycle: Cycle | null;
  onRun: (brief?: string) => Promise<void> | void;
  onHandoff: (brief?: string) => Promise<void> | void;
  onOpenTab: (tab: WorkspaceTab) => void;
  onResearch: () => Promise<void> | void;
  onSelectVersion: (value: string) => boolean;
  onAdvanceProgram: (programId: string) => Promise<void> | void;
}) {
  const [messages, setMessages] = useState<Message[]>([
    { id: "ready", role: "assistant", content: "Ready." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<string[]>(["gpt-5.6-terra"]);
  const [model, setModel] = useState("gpt-5.6-terra");
  const [skill, setSkill] = useState<(typeof SKILLS)[number][0]>("workflow");
  const [actionsEnabled, setActionsEnabled] = useState(true);
  const [bindingInputs, setBindingInputs] = useState<BindingInput[]>([]);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    api
      .get<{ default: string; models: string[] }>("/assistant/models")
      .then((result) => {
        if (!active) return;
        setModels(result.models);
        setModel(result.default);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    setMessages([{ id: `ready-${project?.id ?? "none"}`, role: "assistant", content: "Ready." }]);
    if (!project) {
      setBindingInputs([]);
      return;
    }
    api
      .get<BindingInput[]>(`/projects/${project.id}/binding-inputs`)
      .then((rows) => {
        if (active) setBindingInputs(rows);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [project?.id]);

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages, busy]);

  async function applyAction(action: AssistantAction): Promise<string> {
    switch (action.type) {
      case "run_cycle":
        await onRun(action.value);
        return "Cycle started";
      case "handoff":
        await onHandoff(action.value);
        return "Handed to swarm";
      case "open_tab":
        onOpenTab(action.value as WorkspaceTab);
        return `Opened ${action.value}`;
      case "trigger_research":
        await onResearch();
        return "Research refreshed";
      case "select_version":
        return onSelectVersion(action.value) ? `Selected ${action.value}` : `Version not found: ${action.value}`;
      case "advance_program":
        await onAdvanceProgram(action.value);
        return "Program round started";
    }
  }

  async function send() {
    const prompt = input.trim();
    if (!prompt || !project || busy) return;
    const userMessage: Message = { id: messageId(), role: "user", content: prompt };
    const next = [...messages, userMessage];
    setMessages(next);
    setInput("");
    setBusy(true);
    try {
      const response = await api.post<AssistantChatResponse>(
        `/projects/${project.id}/assistant/chat`,
        {
          messages: next.slice(-10).map(({ role, content }) => ({ role, content })),
          selected_commit_id: selected?.id ?? null,
          cycle_id: cycle?.id ?? null,
          model,
          skill,
          actions_enabled: actionsEnabled,
        },
      );
      const notes: string[] = [];
      if (actionsEnabled) {
        for (const action of response.actions) notes.push(await applyAction(action));
      }
      setMessages((current) => [
        ...current,
        {
          id: messageId(),
          role: "assistant",
          model: response.model,
          actions: response.actions,
          content: `${response.text}${notes.length ? `\n\n${notes.join(" · ")}` : ""}`,
        },
      ]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: messageId(),
          role: "assistant",
          content: error instanceof Error ? error.message : "Chat unavailable.",
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  const context = selected?.label ?? selected?.short_id ?? "No version";
  const inputContext = bindingInputs.map((item) => item.role).join(" + ");

  return (
    <div className="workspace-chat" data-testid="workspace-chat">
      <details className="chat-settings">
        <summary>
          <strong>{model.replace(/^gpt-/, "GPT-")}</strong>
          <span>{SKILLS.find(([id]) => id === skill)?.[1]}</span>
          <em>{actionsEnabled ? "Actions on" : "Advisory"}</em>
        </summary>
        <div>
          <label>
            <span>Model</span>
            <select aria-label="AI model" value={model} onChange={(event) => setModel(event.target.value)}>
              {models.map((item) => (
                <option value={item} key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Skill</span>
            <select
              aria-label="AI skill"
              value={skill}
              onChange={(event) => setSkill(event.target.value as typeof skill)}
            >
              {SKILLS.map(([id, label]) => <option value={id} key={id}>{label}</option>)}
            </select>
          </label>
          <label className="action-toggle tip" data-tip="Lets the agent run existing, versioned workspace operations. It cannot bypass API permissions.">
            <input type="checkbox" checked={actionsEnabled} onChange={(event) => setActionsEnabled(event.target.checked)} />
            Apply
          </label>
        </div>
      </details>

      <div className="chat-context tip" data-tip="The agent receives this selected version plus the active project, cycle, programs and uploaded PDB input names.">
        <span>Context</span>
        <strong>{context}</strong>
        {inputContext ? <small>{inputContext}</small> : null}
      </div>

      <div className="chat-log" ref={logRef} aria-live="polite">
        {messages.map((message) => (
          <div className={`chat-message ${message.role}`} key={message.id}>
            <span>{message.role === "assistant" ? "Agent" : "You"}{message.model ? ` · ${message.model}` : ""}</span>
            <p>{message.content}</p>
            {message.actions?.length ? (
              <div>{message.actions.map((action, index) => (
                <small title={action.reason} key={`${action.type}-${index}`}>{action.type.replaceAll("_", " ")}</small>
              ))}</div>
            ) : null}
          </div>
        ))}
        {busy ? <div className="chat-thinking" aria-label="Agent is working"><i /><i /><i /></div> : null}
      </div>

      <div className="chat-composer">
        <textarea
          aria-label="Ask the workspace agent"
          placeholder="Ask or change anything"
          rows={1}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <button type="button" aria-label="Send" disabled={!project || !input.trim() || busy} onClick={() => void send()}>
          Send
        </button>
      </div>
    </div>
  );
}
