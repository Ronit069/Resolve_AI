import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { IdentityProvider } from "../../state/IdentityContext";
import { Login } from "../Login";
import { login } from "../../api/client";

vi.mock("../../api/client", () => ({
  login: vi.fn(),
  setApiIdentity: vi.fn(),
  setApiAccessToken: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  },
}));

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(login).mockReset();
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

describe("Login", () => {
  it("shows a real sign-in form (email/password) by default", () => {
    renderLogin();
    expect(screen.getByLabelText(/^Email$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Password$/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/User ID/i)).not.toBeInTheDocument();
  });

  it("signs in and stores the returned session via setSession", async () => {
    vi.mocked(login).mockResolvedValue({
      access_token: "jwt-token-abc",
      token_type: "bearer",
      user_id: "11111111-1111-1111-1111-111111111111",
      role: "RISK_ANALYST",
    });

    renderLogin();
    fireEvent.change(screen.getByLabelText(/^Email$/i), { target: { value: "user@test.com" } });
    fireEvent.change(screen.getByLabelText(/^Password$/i), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    await waitFor(() => {
      expect(window.localStorage.getItem("resolveai.accessToken")).toBe("jwt-token-abc");
    });
    expect(window.localStorage.getItem("resolveai.dev.roleLabel")).toBe("RISK_ANALYST");
    expect(login).toHaveBeenCalledWith("user@test.com", "s3cret");
  });

  it("surfaces a failed sign-in's error message instead of navigating", async () => {
    const { ApiError } = await import("../../api/client");
    vi.mocked(login).mockRejectedValue(new ApiError(401, "Incorrect email or password"));

    renderLogin();
    fireEvent.change(screen.getByLabelText(/^Email$/i), { target: { value: "user@test.com" } });
    fireEvent.change(screen.getByLabelText(/^Password$/i), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Incorrect email or password");
    });
    expect(window.localStorage.getItem("resolveai.accessToken")).toBeNull();
  });

  describe("development identity selector (secondary, hidden by default)", () => {
    function revealDevSelector() {
      fireEvent.click(screen.getByRole("button", { name: /development identity selector/i }));
    }

    it("is hidden until toggled, and explicitly labels itself as not a secure login", () => {
      renderLogin();
      expect(screen.queryByText(/not a secure login/i)).not.toBeInTheDocument();

      revealDevSelector();
      expect(screen.getByText(/Development identity selector — not a secure login/i)).toBeInTheDocument();
    });

    it("stores the entered user id and role, and propagates identity via the API client", () => {
      renderLogin();
      revealDevSelector();

      fireEvent.change(screen.getByLabelText(/User ID/i), {
        target: { value: "22222222-2222-2222-2222-222222222222" },
      });
      fireEvent.change(screen.getByLabelText(/Role/i), { target: { value: "RISK_ANALYST" } });
      fireEvent.click(screen.getByRole("button", { name: /continue with dev identity/i }));

      expect(window.localStorage.getItem("resolveai.dev.userId")).toBe(
        "22222222-2222-2222-2222-222222222222"
      );
      expect(window.localStorage.getItem("resolveai.dev.roleLabel")).toBe("RISK_ANALYST");
    });

    it("does not submit an empty user id", () => {
      renderLogin();
      revealDevSelector();
      fireEvent.click(screen.getByRole("button", { name: /continue with dev identity/i }));
      expect(window.localStorage.getItem("resolveai.dev.userId")).toBeNull();
    });
  });
});
