import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { IdentityProvider } from "../../state/IdentityContext";
import { Login } from "../Login";

vi.mock("../../api/client", () => ({
  setApiIdentity: vi.fn(),
}));

beforeEach(() => {
  window.localStorage.clear();
});

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <IdentityProvider>
        <Login />
      </IdentityProvider>
    </MemoryRouter>
  );
}

describe("Login (development identity selector)", () => {
  it("explicitly labels itself as not a secure login", () => {
    renderLogin();
    expect(screen.getByText(/Development identity selector — not a secure login/i)).toBeInTheDocument();
  });

  it("stores the entered user id and role, and propagates identity via the API client", () => {
    renderLogin();
    fireEvent.change(screen.getByLabelText(/User ID/i), {
      target: { value: "22222222-2222-2222-2222-222222222222" },
    });
    fireEvent.change(screen.getByLabelText(/Role/i), { target: { value: "RISK_ANALYST" } });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(window.localStorage.getItem("resolveai.dev.userId")).toBe(
      "22222222-2222-2222-2222-222222222222"
    );
    expect(window.localStorage.getItem("resolveai.dev.roleLabel")).toBe("RISK_ANALYST");
  });

  it("does not submit an empty user id", () => {
    renderLogin();
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(window.localStorage.getItem("resolveai.dev.userId")).toBeNull();
  });
});
