// A row of tabs over a list, one of them chosen (Buildium's Unpaid / Paid tabs on Bills).
export function Tabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
}: {
  label: string;
  tabs: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="tablist" aria-label={label} className="mb-4 flex gap-1 border-b border-line">
      {tabs.map((tab) => {
        const chosen = tab.value === value;
        return (
          <button
            key={tab.value}
            type="button"
            role="tab"
            aria-selected={chosen}
            onClick={() => onChange(tab.value)}
            className={
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium " +
              (chosen ? "border-iris text-ink" : "border-transparent text-ink-muted hover:text-ink")
            }
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
