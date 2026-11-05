import type { SearchFilters } from "../api/client";

type Tri = "" | "true" | "false";
const tri = (v: boolean | null | undefined): Tri => (v == null ? "" : v ? "true" : "false");
const fromTri = (v: string): boolean | null => (v === "" ? null : v === "true");

const REMOTE = ["onsite", "hybrid", "remote"] as const;
const SENIORITY = ["intern", "junior", "mid", "senior", "staff_plus"] as const;
const BLUECARD = ["meets_standard", "meets_reduced_only", "below", "salary_not_stated"] as const;

interface Props {
  value: SearchFilters;
  onChange: (next: SearchFilters) => void;
}

/** All F1 filters. A "controlled" component: the parent owns the state (value) and
 *  gets every change through onChange; this component only renders it. */
export function Filters({ value, onChange }: Props) {
  const set = (patch: Partial<SearchFilters>) => onChange({ ...value, ...patch });
  const toggle = <T extends string>(list: T[] | null | undefined, item: T): T[] | null => {
    const cur = list ?? [];
    const next = cur.includes(item) ? cur.filter((x) => x !== item) : [...cur, item];
    return next.length ? next : null;
  };
  return (
    <fieldset className="filters">
      <legend>Filters</legend>
      <label>
        City
        <input
          value={value.city ?? ""}
          onChange={(e) => set({ city: e.target.value || null })}
          placeholder="e.g. Berlin"
        />
      </label>
      <label>
        Max years of experience
        <input
          type="number"
          min={0}
          max={40}
          value={value.max_years ?? ""}
          onChange={(e) =>
            set({ max_years: e.target.value === "" ? null : Number(e.target.value) })
          }
        />
      </label>
      <label>
        Skills (comma-separated)
        <input
          value={(value.skills ?? []).join(", ")}
          onChange={(e) => {
            const skills = e.target.value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean);
            set({ skills: skills.length ? skills : null });
          }}
          placeholder="python, sql"
        />
      </label>
      <label>
        Posting language
        <select
          value={value.posting_language ?? ""}
          onChange={(e) =>
            set({ posting_language: (e.target.value || null) as SearchFilters["posting_language"] })
          }
        >
          <option value="">any</option>
          <option value="en">English</option>
          <option value="de">German</option>
        </select>
      </label>
      {(
        [
          ["english_sufficient", "English is enough"],
          ["visa_sponsorship", "Visa sponsorship mentioned"],
          ["relocation_support", "Relocation mentioned"],
          ["salary_stated", "Salary stated"],
        ] as const
      ).map(([key, label]) => (
        <label key={key}>
          {label}
          <select value={tri(value[key])} onChange={(e) => set({ [key]: fromTri(e.target.value) })}>
            <option value="">any</option>
            <option value="true">yes</option>
            <option value="false">no</option>
          </select>
        </label>
      ))}
      <div role="group" aria-label="Remote policy" className="chips">
        {REMOTE.map((r) => (
          <label key={r} className="chip">
            <input
              type="checkbox"
              checked={value.remote_policy?.includes(r) ?? false}
              onChange={() => set({ remote_policy: toggle(value.remote_policy, r) })}
            />
            {r}
          </label>
        ))}
      </div>
      <div role="group" aria-label="Seniority" className="chips">
        {SENIORITY.map((r) => (
          <label key={r} className="chip">
            <input
              type="checkbox"
              checked={value.seniority?.includes(r) ?? false}
              onChange={() => set({ seniority: toggle(value.seniority, r) })}
            />
            {r}
          </label>
        ))}
      </div>
      <div role="group" aria-label="Blue Card status" className="chips">
        {BLUECARD.map((r) => (
          <label key={r} className="chip">
            <input
              type="checkbox"
              checked={value.bluecard_status?.includes(r) ?? false}
              onChange={() => set({ bluecard_status: toggle(value.bluecard_status, r) })}
            />
            {r.replace(/_/g, " ")}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
