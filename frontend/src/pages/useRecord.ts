import { useState } from "react";
import { ApiError, api } from "../api";

// Posts one thing staff record and says what it did, or why it was refused; true when it was
// recorded, after which the page is refreshed.
export function useRecord(refresh: () => void) {
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  async function record<T>(path: string, body: unknown, done: (answer: T) => string) {
    setError("");
    setSaid("");
    try {
      setSaid(done(await api.post<T>(path, body)));
      refresh();
      return true;
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
      return false;
    }
  }
  return { said, error, record };
}

export type Recorder = ReturnType<typeof useRecord>["record"];
