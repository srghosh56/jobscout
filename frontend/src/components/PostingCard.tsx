import { Link } from "react-router-dom";
import type { PostingSummary } from "../api/client";
import { shortDate } from "../lib/format";
import { BlueCardBadge, EnglishBadge } from "./Badges";

export function PostingCard({ p, children }: { p: PostingSummary; children?: React.ReactNode }) {
  return (
    <article className="card" aria-labelledby={`t-${p.id}`}>
      <h3 id={`t-${p.id}`}>
        <Link to={`/postings/${p.id}`}>{p.title}</Link>
      </h3>
      <p className="muted">
        {p.company ?? "Unknown company"} · {p.city ?? "location n/a"} · {p.remote_policy}
        {p.min_years_experience != null && ` · ${p.min_years_experience}+ yrs`}
        {p.salary && ` · ${p.salary}`}
      </p>
      <div className="badges">
        <EnglishBadge value={p.english_sufficient} />
        <BlueCardBadge status={p.bluecard_status} />
        {p.visa_sponsorship === "yes" && <span className="badge good">Visa sponsorship</span>}
        {p.relocation_support === "yes" && <span className="badge good">Relocation</span>}
      </div>
      {p.snippet && <p className="snippet">{p.snippet}</p>}
      {p.required_skills.length > 0 && (
        <p className="skills">{p.required_skills.slice(0, 8).join(" · ")}</p>
      )}
      <p className="muted small">Last checked: {shortDate(p.last_checked_at)}</p>
      {children}
    </article>
  );
}
