// For an effect that asks the API again whenever what the page shows changes (a property or
// group picked, a tab, a status, a page of a list, days, a search, another record): an answer to
// what was picked before can come back after the answer to what is picked now, and setting state
// from it would show the old pick's data under the new one. Each run of the effect takes its own
// `latest()`, sets state only through `keep`, and returns `stop` as its cleanup, which React calls
// before the next run (and when the page closes): from then on that run's answers are dropped.
//
//   useEffect(() => {
//     const run = latest();
//     api.get<Paged<Bill>>(`/api/payables/bills?${query}`).then(run.keep(setPage)).catch(run.keep(failed));
//     return run.stop;
//   }, [query]);
//
// A page whose `refresh` also runs after a save takes `keep: Keep = keepAll`: the effect passes its
// run's `keep`, and a save's refresh keeps its answer.
export type Keep = ReturnType<typeof latest>["keep"];

export const keepAll: Keep = (set) => set;

export function latest() {
  let current = true;
  return {
    keep:
      <A extends unknown[]>(set: (...args: A) => void) =>
      (...args: A) => {
        if (current) set(...args);
      },
    stop: () => {
      current = false;
    },
  };
}
