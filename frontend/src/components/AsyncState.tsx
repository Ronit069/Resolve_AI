import type { ReactNode } from "react";
import { ApiError } from "../api/client";

interface AsyncStateProps<T> {
  loading: boolean;
  error: unknown;
  data: T | null | undefined;
  isEmpty?: (data: T) => boolean;
  emptyMessage?: ReactNode;
  loadingMessage?: ReactNode;
  treat404AsEmpty?: boolean;
  children: (data: T) => ReactNode;
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Not authenticated. Select a development identity to continue.";
    if (error.status === 403) return "Your current role is not authorized for this.";
    if (error.status === 404) return "Not found.";
    if (error.status === 422) return "Missing or invalid request (development identity required).";
    return error.detail;
  }
  return "Something went wrong loading this data.";
}

/**
 * Shared loading / empty / error presentation used by every Module I
 * screen. Read-only wrapper — it never mutates anything, only decides what
 * to render for the three states every screen documented in the blueprint
 * needs (loading, empty, error).
 */
export function AsyncState<T>({
  loading,
  error,
  data,
  isEmpty,
  emptyMessage = "Nothing to show yet.",
  loadingMessage = "Loading…",
  treat404AsEmpty = false,
  children,
}: AsyncStateProps<T>) {
  if (loading) {
    return <div role="status">{loadingMessage}</div>;
  }
  
  const is404Error = !!error && typeof ApiError !== "undefined" && error instanceof ApiError && error.status === 404;
  
  if (error && !(treat404AsEmpty && is404Error)) {
    return <div role="alert">{describeError(error)}</div>;
  }
  
  if (data == null || (isEmpty && isEmpty(data)) || (treat404AsEmpty && is404Error)) {
    return <div role="status">{emptyMessage}</div>;
  }
  return <>{children(data)}</>;
}
