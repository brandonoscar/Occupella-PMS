// Tests only: holds back the answer to a request until the test lets it go, so an answer to what
// was picked before comes back after the answer to what is picked now (latest.ts).
//
// It wraps the fetch the test already stubbed: the first request `held` picks waits, every other
// one is answered at once, and `release()` lets the held one through and waits for the page to
// take it in. Each test that uses it should fail without its page's `latest()`.
import { vi } from "vitest";

export function holdBack(held: (path: string) => boolean) {
  const answer = globalThis.fetch;
  let open = () => {};
  const gate = new Promise<void>((resolve) => {
    open = resolve;
  });
  let waiting = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (waiting && held(String(path))) {
        waiting = false;
        await gate;
      }
      return answer(path, init);
    }),
  );
  return async () => {
    open();
    await new Promise((resolve) => setTimeout(resolve, 30));
  };
}
