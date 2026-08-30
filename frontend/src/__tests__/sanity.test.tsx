/**
 * Sanity test — verifies vitest + RTL + jsdom + setup polyfills boot correctly.
 * Delete once the first real component test lands.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "../test-utils/render";

describe("vitest foundation", () => {
  it("renders a trivial component", () => {
    render(<button type="button">click me</button>);
    expect(screen.getByRole("button", { name: /click me/i })).toBeInTheDocument();
  });

  it("polyfills ResizeObserver and IntersectionObserver", () => {
    expect(globalThis.ResizeObserver).toBeDefined();
    expect(globalThis.IntersectionObserver).toBeDefined();
  });
});
