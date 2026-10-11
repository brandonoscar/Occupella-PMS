// Calls to the API on the same origin. The session is an HttpOnly cookie the browser sends by
// itself; every change goes as JSON, which the API requires (it refuses anything else, so a
// form on another site can't act for the person signed in here).

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
  }
}

export type Me = {
  kind: "staff" | "owner" | "tenant";
  email: string;
  name: string;
  person_id: string;
  pmc_id: string;
  pmc_name: string;
};

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) {
    return undefined as T;
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, detailOf(data) ?? `the server answered ${response.status}`);
  }
  return data as T;
}

function detailOf(data: unknown): string | null {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return "check what you entered and try again";
  }
  return null;
}

export const api = {
  get: <T>(path: string) => call<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => call<T>("POST", path, body),
};
