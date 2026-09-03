import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { IdentityProvider } from "../../state/IdentityContext";
import { RoleGate } from "../RoleGate";

vi.mock("../../api/client", () => ({
  setApiIdentity: vi.fn(),
}));

beforeEach(() => {
  window.localStorage.clear();
});

describe("RoleGate (UX-only)", () => {
  it("renders children when the client-side role label is allowed", () => {
    window.localStorage.setItem("resolveai.dev.roleLabel", "APPROVER");
    render(
      <IdentityProvider>
        <RoleGate allow={["APPROVER"]}>
          <button>Submit</button>
        </RoleGate>
      </IdentityProvider>
    );
    expect(screen.getByRole("button", { name: "Submit" })).toBeInTheDocument();
  });

  it("renders the fallback when the client-side role label is not allowed", () => {
    window.localStorage.setItem("resolveai.dev.roleLabel", "RISK_ANALYST");
    render(
      <IdentityProvider>
        <RoleGate allow={["APPROVER"]} fallback={<p>Not authorized</p>}>
          <button>Submit</button>
        </RoleGate>
      </IdentityProvider>
    );
    expect(screen.queryByRole("button", { name: "Submit" })).not.toBeInTheDocument();
    expect(screen.getByText("Not authorized")).toBeInTheDocument();
  });
});
