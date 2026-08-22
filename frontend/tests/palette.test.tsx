import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import CommandPalette from "@/components/CommandPalette";

describe("command palette", () => {
  const commands = [
    { id: "run", label: "run design cycle", hint: "⌘↵", run: vi.fn() },
    { id: "cancel", label: "cancel running cycle", disabled: true, run: vi.fn() },
    { id: "seed", label: "load demo project", run: vi.fn() },
  ];

  it("hides disabled commands and runs the selected one", () => {
    const onClose = vi.fn();
    render(<CommandPalette open commands={commands} onClose={onClose} />);
    expect(screen.getAllByRole("button")).toHaveLength(2);
    fireEvent.change(screen.getByLabelText("command"), { target: { value: "demo" } });
    fireEvent.keyDown(screen.getByLabelText("command"), { key: "Enter" });
    expect(commands[2].run).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("renders nothing when closed", () => {
    render(<CommandPalette open={false} commands={commands} onClose={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
