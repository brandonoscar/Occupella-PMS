import { afterEach, describe, expect, it, vi } from "vitest";
import { holdBack } from "./holdBack";
import { keepAll, latest } from "./latest";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("latest", () => {
  it("sets state from a run's answers until the run is stopped, then drops them", () => {
    const set = vi.fn();
    const run = latest();

    run.keep(set)("first", 1);
    run.stop();
    run.keep(set)("second", 2);

    expect(set.mock.calls).toEqual([["first", 1]]);
  });

  it("keeps each run's answers apart: stopping one leaves the next one's", () => {
    const shown: string[] = [];
    const before = latest();
    const now = latest();
    const show = (answer: string) => shown.push(answer);

    before.stop();
    now.keep(show)("now");
    before.keep(show)("before");

    expect(shown).toEqual(["now"]);
  });

  it("keeps every answer of a read made outside such an effect, after a save", () => {
    const set = vi.fn();

    keepAll(set)("saved");

    expect(set.mock.calls).toEqual([["saved"]]);
  });
});

describe("holdBack", () => {
  it("answers the held request only once released, after the ones asked later", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(path)));
    const release = holdBack((path) => path === "/before");
    const order: string[] = [];

    const before = fetch("/before").then(async (answer) => order.push(await answer.text()));
    await fetch("/now").then(async (answer) => order.push(await answer.text()));
    expect(order).toEqual(["/now"]);
    await release();
    await before;

    expect(order).toEqual(["/now", "/before"]);
  });

  it("holds back only the first request it picks", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(path)));
    const release = holdBack((path) => path === "/again");

    const first = fetch("/again");
    expect(await (await fetch("/again")).text()).toBe("/again");
    await release();
    expect(await (await first).text()).toBe("/again");
  });
});
