// The page every portal screen is served in: its tab names the product, as the README does.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

// vitest runs from frontend/, where index.html is.
const PAGE = readFileSync(join(process.cwd(), "index.html"), "utf8");

it("names the product in the browser tab", () => {
  expect(PAGE).toContain("<title>Occupella PMS</title>");
});
