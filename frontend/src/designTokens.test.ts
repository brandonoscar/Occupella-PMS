// Occupella's DESIGN.md rules that a file can be checked for. A component uses the tokens in
// styles/globals.css: no raw colour, no Tailwind palette class, no gradient, no glass blur,
// nothing bolder than 600, and only the 12/13/14/16/20/28px type scale.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// vitest runs from frontend/, where package.json is.
const SRC = join(process.cwd(), "src");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

const FILES = sources(SRC).map((path) => ({ path, text: readFileSync(path, "utf8") }));
const PALETTE =
  /\b(bg|text|border|ring|outline|fill|stroke|from|via|to|shadow)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|black|white)(-\d{2,3})?\b/;
const FORBIDDEN: [string, RegExp][] = [
  ["a raw colour", /#[0-9a-fA-F]{3,8}\b|\brgba?\(/],
  ["a Tailwind palette class", PALETTE],
  ["a gradient", /gradient/],
  ["a glass blur", /backdrop-blur|backdrop-filter/],
  ["a weight above 600", /\bfont-(bold|extrabold|black)\b/],
  ["a size off the scale", /\btext-\[\d+px\]|\btext-(2xl|3xl|4xl|5xl)\b/],
];

describe("design tokens", () => {
  it("finds the components to check", () => {
    expect(FILES.length).toBeGreaterThan(5);
  });

  for (const [what, pattern] of FORBIDDEN) {
    it(`no component uses ${what}`, () => {
      expect(FILES.filter((file) => pattern.test(file.text)).map((file) => file.path)).toEqual([]);
    });
  }

  // Occupella's DESIGN.md: mono "for IDs, amounts and code only, never for labels". Here it is for
  // links, keys and hashes, which break anywhere, so a mono class always comes with break-all;
  // money and dates are set in the UI face like everything else.
  it("mono is only for links, keys and hashes", () => {
    const uses = FILES.flatMap((file) =>
      [...file.text.matchAll(/"[^"\n]*\bfont-mono\b[^"\n]*"/g)].map((match) => ({ path: file.path, classes: match[0] })),
    );
    const all = FILES.reduce((n, file) => n + (file.text.match(/\bfont-mono\b/g) ?? []).length, 0);
    expect(uses.length).toBeGreaterThan(0);
    expect(uses.length).toBe(all); // every mono class is in a class string this test reads
    expect(uses.filter((use) => !/\bbreak-all\b/.test(use.classes))).toEqual([]);
  });

  it("the type scale is 12, 13, 14, 16, 20 and 28px", () => {
    const css = readFileSync(join(SRC, "styles/globals.css"), "utf8");
    const sizes = [...css.matchAll(/--text-[a-z]+:\s*(\d+)px/g)].map((match) => Number(match[1]));
    expect(sizes).toEqual([12, 13, 14, 16, 20, 28]);
  });

  it("the accent is occupella.com's blue", () => {
    const css = readFileSync(join(SRC, "styles/globals.css"), "utf8");
    expect(css).toContain("--color-iris: #1957a0;");
  });

  // The PMS looks like Occupella's app: these are the values in its frontend/src/styles/globals.css
  // (white canvas, the light-blue sidebar rail, ink text, its status colours). Change one, change both.
  it("the colours are Occupella's", () => {
    const css = readFileSync(join(SRC, "styles/globals.css"), "utf8");
    const tokens = Object.fromEntries(
      [...css.matchAll(/--color-([a-z-]+):\s*([^;]+);/g)].map((match) => [match[1], match[2]]),
    );
    expect(tokens).toMatchObject({
      canvas: "#ffffff",
      raised: "#f4f8fd",
      ink: "#0e1620",
      "ink-muted": "#55606e",
      "ink-subtle": "#6b7481",
      chrome: "#e4eef7",
      "chrome-raised": "#ffffff",
      "chrome-ink": "#12233a",
      "chrome-ink-muted": "#4c5c72",
      "chrome-accent": "#1957a0",
      titlebar: "#ffffff",
      positive: "#1d7a4c",
      caution: "#a8631a",
      danger: "#b3261e",
      info: "#4a5a6b",
    });
    expect(css).toContain("color-scheme: light;");
  });
});
