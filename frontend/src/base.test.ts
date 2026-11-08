// The portal under a path (src/base.ts): at the root self-hosted, at /books/ in Occupella's
// hosted app. Two halves:
//  - what withBase(), withoutBase(), the router and the API client do under "/" and "/books/";
//  - a scan of every place a URL reaches the browser (a link's href, an image's src, fetch(),
//    the history and location), so a page added later can't send someone to /properties when
//    the portal lives at /books/properties. The scan reads the sinks, not where a path came
//    from: whatever a helper returns, it passes through withBase() where the browser gets it.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { withBase, withoutBase } from "./base";
import { leave, navigate, usePath } from "./router";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("at the root (self-hosted)", () => {
  it("leaves every path as it is", () => {
    vi.stubEnv("BASE_URL", "/");
    expect(withBase("/properties")).toBe("/properties");
    expect(withBase("/api/me")).toBe("/api/me");
    expect(withBase("/")).toBe("/");
    expect(withoutBase("/properties")).toBe("/properties");
    expect(withoutBase("/")).toBe("/");
  });
});

describe("under /books/ (Occupella's hosted app)", () => {
  it("puts a page or an API path under it", () => {
    vi.stubEnv("BASE_URL", "/books/");
    expect(withBase("/properties")).toBe("/books/properties");
    expect(withBase("/leases/abc?tab=ledger#top")).toBe("/books/leases/abc?tab=ledger#top");
    expect(withBase("/api/documents/d1/file")).toBe("/books/api/documents/d1/file");
    expect(withBase("/")).toBe("/books/");
  });

  it("changes nothing passed through twice, nor another site's address", () => {
    vi.stubEnv("BASE_URL", "/books/");
    expect(withBase(withBase("/api/me"))).toBe("/books/api/me");
    expect(withBase("/books")).toBe("/books");
    for (const url of ["https://maps.example.com/x", "mailto:a@example.com", "tel:+15555550123", "#top", "//cdn.example.com/x", "data:image/png;base64,AA"]) {
      expect(withBase(url)).toBe(url);
    }
  });

  it("takes it off the browser's path, and leaves a path outside it alone", () => {
    vi.stubEnv("BASE_URL", "/books/");
    expect(withoutBase("/books/properties")).toBe("/properties");
    expect(withoutBase("/books/")).toBe("/");
    expect(withoutBase("/books")).toBe("/");
    expect(withoutBase("/bookshelf")).toBe("/bookshelf");
  });

  it("navigates under it, and the pages read the path without it", () => {
    vi.stubEnv("BASE_URL", "/books/");
    window.history.replaceState(null, "", "/books/rent-roll");
    const { result } = renderHook(() => usePath());
    expect(result.current).toBe("/rent-roll");
    act(() => navigate("/leases/l1"));
    expect(window.location.pathname).toBe("/books/leases/l1");
    expect(result.current).toBe("/leases/l1");
  });

  it("leaves for a page under it, and for another site as it is", () => {
    vi.stubEnv("BASE_URL", "/books/");
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    leave("/pay/done?paid=1");
    leave("https://checkout.example.com/c/1");
    expect(assign.mock.calls).toEqual([["/books/pay/done?paid=1"], ["https://checkout.example.com/c/1"]]);
  });

  it("calls the API under it", async () => {
    vi.stubEnv("BASE_URL", "/books/");
    const fetched = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetched);
    await api.get("/api/me");
    await api.post("/api/sign-out");
    expect(fetched.mock.calls.map((call) => (call as unknown[])[0])).toEqual(["/books/api/me", "/books/api/sign-out"]);
  });
});

// --- the scan -------------------------------------------------------------------------------

// vitest runs from frontend/, where package.json is.
const SRC = join(process.cwd(), "src");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) && name !== "testSetup.ts" ? [path] : [];
  });
}

/** The file with its comments blanked out (strings and template literals kept), so the words
 * explaining a rule are never read as code. */
function code(text: string): string {
  let out = "";
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    const next = text[i + 1];
    if (c === "/" && next === "/") {
      while (i < text.length && text[i] !== "\n") i++;
    } else if (c === "/" && next === "*") {
      const end = text.indexOf("*/", i + 2);
      i = end < 0 ? text.length : end + 2;
    } else if (c === '"' || c === "'" || c === "`") {
      const start = i++;
      while (i < text.length && text[i] !== c) i += text[i] === "\\" ? 2 : 1;
      out += text.slice(start, ++i);
    } else {
      out += c;
      i++;
    }
  }
  return out;
}

/** From `from`, past a balanced {...} or quoted value; returns the index after it. */
function pastValue(text: string, from: number): number {
  const open = text[from];
  if (open === '"' || open === "'") return text.indexOf(open, from + 1) + 1;
  let depth = 0;
  for (let i = from; i < text.length; i++) {
    const c = text[i];
    if (c === '"' || c === "'" || c === "`") {
      i++;
      while (i < text.length && text[i] !== c) i += text[i] === "\\" ? 2 : 1;
    } else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return i + 1;
  }
  return text.length;
}

type Sink = { file: string; tag: string; attr: string; value: string };

/** Every `<a href=...>` and `<img src=...>` the portal renders, with the value written there. */
function linkSinks(): Sink[] {
  const found: Sink[] = [];
  for (const path of sources(SRC)) {
    const text = code(readFileSync(path, "utf8"));
    for (const [tag, attr] of [["a", "href"], ["img", "src"]] as const) {
      const opening = new RegExp(`<${tag}(?=[\\s>/])`, "g");
      for (const match of text.matchAll(opening)) {
        let i = (match.index ?? 0) + match[0].length;
        while (i < text.length && text[i] !== ">") {
          if (text[i] === "{" || text[i] === '"' || text[i] === "'") {
            const end = pastValue(text, i);
            const before = text.slice(0, i).match(/(\w[\w-]*)=\s*$/);
            if (before?.[1] === attr) {
              found.push({ file: relative(SRC, path), tag, attr, value: text.slice(i, end) });
            }
            i = end;
          } else i++;
        }
      }
    }
  }
  return found;
}

// What a link or image may carry without withBase(): another site, an email or a phone number,
// a fragment, a generated image, a calendar's subscribe address.
const NOT_A_PATH = /^\{?\s*(["'`](mailto:|tel:|https?:|data:|webcal:|#)|(qrDataUrl|webcal)\()/;

describe("every URL the browser gets passes through withBase()", () => {
  const sinks = linkSinks();

  it("finds the links and images to check", () => {
    // A scan that finds nothing is a claim to check, not a result.
    expect(sinks.filter((s) => s.tag === "a").length).toBeGreaterThan(30);
    expect(sinks.filter((s) => s.tag === "img").length).toBeGreaterThan(3);
  });

  it("each link's href and image's src", () => {
    const bare = sinks.filter((s) => !/^\{\s*withBase\(/.test(s.value) && !NOT_A_PATH.test(s.value));
    expect(bare.map((s) => `${s.file}: <${s.tag} ${s.attr}=${s.value}>`)).toEqual([]);
  });

  it("each fetch, page change and redirect", () => {
    const found: string[] = [];
    for (const path of sources(SRC)) {
      const file = relative(SRC, path);
      const text = code(readFileSync(path, "utf8"));
      const where = (pattern: RegExp, allowed: string[]) => {
        if (pattern.test(text) && !allowed.includes(file)) found.push(`${file}: ${pattern.source}`);
      };
      // The API client and the router are the only places that fetch, move or read the page.
      where(/\bfetch\(/, ["api.ts"]);
      where(/location\.(assign|replace)\(|window\.open\(|location\.href\s*=[^=]/, ["router.ts"]);
      where(/history\.pushState\(/, ["router.ts"]);
      where(/history\.replaceState\(/, ["router.ts", "scope.ts"]);
      where(/location\.pathname/, ["router.ts", "base.ts"]);
      if (file === "api.ts") {
        for (const call of text.matchAll(/\bfetch\(([^,)]*)/g)) {
          if (!/^withBase\(/.test(call[1] ?? "")) found.push(`api.ts: fetch(${call[1]}) without withBase()`);
        }
      }
      // An address built from this site's origin takes the portal's path too.
      for (const built of text.matchAll(/\$\{origin\}([^`]*)/g)) {
        if (!/^\$\{withBase\(/.test(built[1] ?? "")) found.push(`${file}: \${origin}${built[1]}`);
      }
    }
    expect(found).toEqual([]);
  });

  it("the router and the API client add it themselves", () => {
    const router = code(readFileSync(join(SRC, "router.ts"), "utf8"));
    expect(router).toMatch(/pushState\(null, "", target\)/);
    expect(router).toMatch(/const target = withBase\(to\)/);
    expect(router).toMatch(/location\.assign\(withBase\(url\)\)/);
    expect(router).toMatch(/withoutBase\(window\.location\.pathname\)/);
  });
});
