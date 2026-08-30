/**
 * Unit tests for usePermissions — the role-based authorization map.
 *
 * This is real access-control logic: a wrong mapping either hides a control
 * from someone who should have it, or worse, exposes a destructive action
 * (delete_data, process_layer) to a role that shouldn't. Mock the app-state
 * context to pin the role, then assert each role's capability set.
 */
import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

vi.mock("../AppStateContext", () => ({ useAppState: vi.fn() }));

import { useAppState } from "../AppStateContext";
import { usePermissions } from "./permissions";
import type { Role } from "../types";

function asRole(role: string) {
  (useAppState as unknown as ReturnType<typeof vi.fn>).mockReturnValue({ role });
  return renderHook(() => usePermissions()).result.current;
}

describe("usePermissions", () => {
  it("grants PLATFORM_ADMIN every permission", () => {
    const { can } = asRole("PLATFORM_ADMIN");
    for (const p of [
      "delete_data",
      "process_layer",
      "edit_conflict",
      "toggle_qdrant",
      "add_chunk_version",
    ] as const) {
      expect(can(p)).toBe(true);
    }
  });

  it("limits AI_ENGINEER to qdrant + extraction policy", () => {
    const { can } = asRole("AI_ENGINEER");
    expect(can("toggle_qdrant")).toBe(true);
    expect(can("edit_extraction_policy")).toBe(true);
    expect(can("delete_data")).toBe(false);
    expect(can("process_layer")).toBe(false);
  });

  it("limits EXECUTIVE to conflict + layer + filtering policy", () => {
    const { can } = asRole("EXECUTIVE");
    expect(can("edit_conflict")).toBe(true);
    expect(can("process_layer")).toBe(true);
    expect(can("add_filtering_policy")).toBe(true);
    expect(can("delete_data")).toBe(false);
    expect(can("toggle_qdrant")).toBe(false);
  });

  it("denies everything for an unknown role", () => {
    const { can } = asRole("SOMETHING_ELSE" as Role);
    expect(can("delete_data")).toBe(false);
    expect(can("edit_conflict")).toBe(false);
  });

  it("never grants a destructive permission to non-admin roles", () => {
    for (const role of ["AI_ENGINEER", "EXECUTIVE"] as const) {
      expect(asRole(role).can("delete_data")).toBe(false);
    }
  });
});
