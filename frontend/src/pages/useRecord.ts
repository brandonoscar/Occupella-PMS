import { useState } from "react";
import { ApiError, api } from "../api";

// Posts one thing staff record and says what it did, or why it was refused; true when it was
// recorded, after which the page is refreshed. A row the refresh may take off the list (a bill
// paid or declined) passes `say`, so what it did is said by the list around it and stays.
export function useRecord(refresh: () => void, say?: (said: string) => void) {
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  const tell = say ?? setSaid;
  async function record<T>(path: string, body: unknown, done: (answer: T) => string) {
    setError("");
    tell("");
    try {
      tell(done(await api.post<T>(path, body)));
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
