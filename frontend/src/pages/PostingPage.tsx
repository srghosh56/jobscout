import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { BlueCardBadge, EnglishBadge } from "../components/Badges";
import { shortDate } from "../lib/format";

export function PostingPage() {
  const { id = "" } = useParams(); // reads :id from the URL
  const { data: p, error } = useQuery({
    queryKey: ["posting", id],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/postings/{posting_id}", {
        params: { path: { posting_id: id } },
      });
      if (error) throw new Error("Posting not found");
      return data;
    },
  });
  if (error) return <p role="alert">{error.message}</p>;
  if (!p) return <p>Loading…</p>;
  const bc = p.bluecard as
    { explanation?: string; disclaimer?: string; source_url?: string } | null | undefined;
  return (
    <article>
      <h1>{p.title}</h1>
      <p className="muted">
        {p.company} · {p.city} · {p.remote_policy} ·{" "}
        {p.posting_language === "de" ? "ad in German" : "ad in English"} · last checked{" "}
        {shortDate(p.last_checked_at)} {p.is_active ? "" : "· no longer online"}
      </p>
      <div className="badges">
        <EnglishBadge value={p.english_sufficient} />
        <BlueCardBadge status={p.bluecard_status} />
      </div>
      <dl className="fields">
        <dt>Seniority</dt>
        <dd>{p.seniority}</dd>
        <dt>Min. years</dt>
        <dd>{p.min_years_experience ?? "not stated"}</dd>
        <dt>German level</dt>
        <dd>{p.german_level}</dd>
        <dt>Languages</dt>
        <dd>
          {(p.languages_required ?? [])
            .map((l) => `${String(l.language)} (${String(l.level)})`)
            .join(", ") || "not stated"}
        </dd>
        <dt>Salary</dt>
        <dd>{p.salary ?? "not stated"}</dd>
        <dt>Contract</dt>
        <dd>{p.contract_type}</dd>
        <dt>Visa sponsorship</dt>
        <dd>{p.visa_sponsorship}</dd>
        <dt>Relocation</dt>
        <dd>{p.relocation_support}</dd>
        <dt>Required skills</dt>
        <dd>{p.required_skills.join(", ") || "–"}</dd>
        <dt>Nice to have</dt>
        <dd>{(p.nice_to_have_skills ?? []).join(", ") || "–"}</dd>
      </dl>
      {bc && (
        <section className="bluecard">
          <h2>EU Blue Card check</h2>
          <p>{bc.explanation}</p>
          <p className="warning">{bc.disclaimer}</p>
          {bc.source_url && <a href={bc.source_url}>Official thresholds (Bundesanzeiger)</a>}
        </section>
      )}
      <p>
        <a className="button" href={p.url} target="_blank" rel="noopener noreferrer">
          Read the original ad ↗
        </a>
      </p>
    </article>
  );
}
