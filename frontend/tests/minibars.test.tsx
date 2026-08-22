import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import MiniBars from "@/components/MiniBars";

describe("MiniBars", () => {
  it("keeps the label a keyboard-reachable tooltip host and truncates on an inner span", () => {
    const { container } = render(
      <MiniBars
        max={10}
        bars={[
          {
            key: "tm",
            name: "predicted melting temperature",
            value: 5,
            readout: "55.3 °C",
            tip: "55.34 ± 4.0 °C against a ≥ 45 °C gate",
          },
        ]}
      />,
    );

    const name = container.querySelector(".barrow .name") as HTMLElement;
    expect(name.getAttribute("data-tip")).toContain("45 °C gate");
    expect(name.tabIndex).toBe(0);
    // the tooltip renders off the label box, so the ellipsis must live on a child
    const clip = name.querySelector(".clip");
    expect(clip?.textContent).toBe("predicted melting temperature");
  });

  it("scales bar widths against the ceiling", () => {
    const { container } = render(
      <MiniBars
        bars={[
          { key: "a", name: "a", value: 2, readout: "2", tip: "a" },
          { key: "b", name: "b", value: 4, readout: "4", tip: "b" },
        ]}
      />,
    );
    const fills = Array.from(container.querySelectorAll(".barfill")) as HTMLElement[];
    expect(fills[0].style.width).toBe("50%");
    expect(fills[1].style.width).toBe("100%");
  });
});
