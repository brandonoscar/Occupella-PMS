import { notify } from "./toast";

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
  // A staff member's roles (src/pms/api/staff.py); none for an owner or a tenant.
  roles?: Role[];
  // The areas a staff member may change (src/pms/api/deps.py AREAS): every one unless their
  // custom roles say otherwise.
  areas?: string[];
  // Whether the server sends email (PMS_EMAIL_SENDER); without it, emails wait to send.
  email_on?: boolean;
  // Whether this login signs in with a code from an authenticator app too (staff always do), and
  // whether it must: always for staff, for an owner or a tenant when their company requires it.
  two_step?: boolean;
  two_step_required?: boolean;
};
export type Role = "admin" | "approve" | "disburse";

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

// How FastAPI words a refusal raised by one of the API's own validators (pydantic's ValueError).
const OWN_RULE = "Value error, ";

function detailOf(data: unknown): string | null {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      // A rule of the PMS's own ("a mailing address needs its street, city, state and ZIP code")
      // says what it is; a field of the wrong kind or length only that something isn't right.
      const rule = detail.find(
        (d): d is { msg: string } => typeof d?.msg === "string" && d.msg.startsWith(OWN_RULE),
      );
      return rule ? rule.msg.slice(OWN_RULE.length) : "check what you entered and try again";
    }
  }
  return null;
}

// A refused change also shows as a toast with its reason, wherever the page says it.
async function change<T>(path: string, body: unknown): Promise<T> {
  try {
    return await call<T>("POST", path, body);
  } catch (failed) {
    notify("error", failed instanceof ApiError ? failed.detail : String(failed));
    throw failed;
  }
}

export const api = {
  get: <T>(path: string) => call<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => change<T>(path, body),
};

// A file the server answers a change with (the signing page's lease and certificate: its token
// travels in the JSON body, never in a URL), saved by the browser under `name`. A refusal shows
// as a toast with its reason, like any change's.
export async function saveFile(path: string, body: unknown, name: string): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    const failed = new ApiError(response.status, detailOf(data) ?? `the server answered ${response.status}`);
    notify("error", failed.detail);
    throw failed;
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
