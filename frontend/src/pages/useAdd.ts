import { useState } from "react";
import { ApiError, api } from "../api";
import type { Portfolio } from "../setup";

// Posts to /api/setup/<path> for one card: true when it was added, else the card shows why.
export function useAdd(refresh: () => void) {
  const [error, setError] = useState("");
  async function add(path: string, body: unknown): Promise<boolean> {
    setError("");
    try {
      await api.post(`/api/setup/${path}`, body);
      refresh();
      return true;
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
      return false;
    }
  }
  return { error, add };
}

export type CardProps = { books: Portfolio; refresh: () => void };
