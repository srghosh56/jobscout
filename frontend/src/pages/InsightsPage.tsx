import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Insights } from "../api/client";
import { pct, shortDate } from "../lib/format";

export function InsightsPage() {
  const { data, error } = useQuery({
    queryKey: ["insights"],
    queryFn: async () => {
      const res = await fetch("/api/insights");
      if (!res.ok) throw new Error("Could not load insights");
      return (await res.json()) as Insights;
    },
  });
  if (error) return <p role="alert">{error.message}</p>;
  if (!data) return <p>Loading…</p>;
  const asPct = (v: number) => `${Math.round(v * 100)}%`;
  return (
    <section>
      <h1>Market insights</h1>
      <p className="muted">
        {data.coverage.active_postings} active postings · last ingested{" "}
        {shortDate(data.coverage.last_ingested)} · {data.coverage.llm_extracted} LLM-extracted,{" "}
        {data.coverage.rules_extracted} rule-extracted
      </p>
      <h2>English-friendly, salary stated, visa/relocation by city</h2>
      <div className="chart" role="img" aria-label="Shares by city">
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={data.by_city}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="city" />
            <YAxis tickFormatter={asPct} />
            <Tooltip formatter={(v) => pct(Number(v))} />
            <Legend />
            <Bar dataKey="share_english_friendly" name="English-friendly" fill="#2563eb" />
            <Bar dataKey="share_salary_stated" name="Salary stated" fill="#16a34a" />
            <Bar dataKey="share_visa_or_relocation" name="Visa/relocation" fill="#d97706" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <h2>By job family</h2>
      <table>
        <thead>
          <tr>
            <th>Job family</th>
            <th>Postings</th>
            <th>English-friendly</th>
            <th>Salary stated</th>
            <th>Visa/relocation</th>
          </tr>
        </thead>
        <tbody>
          {data.by_job_family.map((f) => (
            <tr key={f.job_family}>
              <td>{f.job_family.replace(/_/g, " ")}</td>
              <td>{f.n}</td>
              <td>{pct(f.share_english_friendly)}</td>
              <td>{pct(f.share_salary_stated)}</td>
              <td>{pct(f.share_visa_or_relocation)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>New postings per week</h2>
      <div className="chart" role="img" aria-label="New postings per week">
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={data.weekly}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="week" />
            <YAxis />
            <Tooltip />
            <Line dataKey="new_postings" name="New postings" stroke="#2563eb" />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <h2>Top skills by role</h2>
      <div className="grid">
        {Object.entries(data.top_skills).map(([family, skills]) => (
          <div key={family} className="card">
            <h3>{family.replace(/_/g, " ")}</h3>
            <ol>
              {skills.map((s) => (
                <li key={s.skill}>
                  {s.skill} ({s.n})
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>
      <h2>Methodology</h2>
      <p className="methodology">{data.methodology}</p>
    </section>
  );
}
