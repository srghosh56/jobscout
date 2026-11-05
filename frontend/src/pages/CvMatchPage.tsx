import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { getApiKey, type CvMatchResponse } from "../api/client";
import { PostingCard } from "../components/PostingCard";

export function CvMatchPage() {
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [store, setStore] = useState(false);
  const [city, setCity] = useState("");

  // useMutation is TanStack Query's helper for actions that change or post data.
  const match = useMutation({
    mutationFn: async (): Promise<CvMatchResponse> => {
      const form = new FormData();
      if (file) form.append("file", file);
      else form.append("cv_text", text);
      form.append("store_profile", String(store));
      if (city) form.append("city", city);
      const res = await fetch("/api/cv/match", {
        method: "POST",
        body: form,
        headers: { "X-API-Key": await getApiKey() },
      });
      if (!res.ok)
        throw new Error(
          ((await res.json()) as { detail?: string }).detail ?? `Error ${res.status}`,
        );
      return (await res.json()) as CvMatchResponse;
    },
  });

  return (
    <section>
      <h1>CV match</h1>
      <p className="muted">
        Your CV is processed in memory and not stored unless you tick the box below. Contact details
        are removed before analysis.
      </p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          match.mutate();
        }}
      >
        <label>
          Upload CV (PDF or .txt, max 2 MB)
          <input
            type="file"
            accept=".pdf,.txt,application/pdf,text/plain"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <label>
          …or paste the text
          <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        <label>
          Preferred city (optional)
          <input value={city} onChange={(e) => setCity(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={store} onChange={(e) => setStore(e.target.checked)} />
          Store my structured profile (no CV text) so I can reuse it. I can delete it at any time.
        </label>
        <button type="submit" disabled={match.isPending || (!file && text.length < 50)}>
          {match.isPending ? "Matching…" : "Find matching jobs"}
        </button>
      </form>
      {match.error && <p role="alert">{match.error.message}</p>}
      {match.data && (
        <>
          <p role="status">{match.data.privacy}</p>
          <h2>Your profile</h2>
          <p>
            {(match.data.profile.skills ?? []).join(", ")}
            {match.data.profile.years_experience != null &&
              ` · ${match.data.profile.years_experience} years`}
          </p>
          <h2>Ranked postings</h2>
          {match.data.matches.map((m) => (
            <PostingCard key={m.posting.id} p={m.posting}>
              <p className="explain">{m.explanation}</p>
              <ul className="gaps">
                {m.gaps.gaps.map((g, i) => (
                  <li key={i} className={`gap ${g.severity}`}>
                    {g.severity}: {g.detail}
                  </li>
                ))}
              </ul>
            </PostingCard>
          ))}
        </>
      )}
    </section>
  );
}
