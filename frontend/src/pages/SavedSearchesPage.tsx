import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";

export function SavedSearchesPage() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [city, setCity] = useState("");
  const list = useQuery({
    queryKey: ["saved"],
    queryFn: async () => (await api.GET("/api/saved-searches")).data ?? [],
  });
  const create = useMutation({
    mutationFn: async () =>
      api.POST("/api/saved-searches", { body: { name, query, filters: city ? { city } : {} } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved"] }),
  });
  const remove = useMutation({
    mutationFn: async (id: string) =>
      api.DELETE("/api/saved-searches/{saved_id}", { params: { path: { saved_id: id } } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved"] }),
  });
  return (
    <section>
      <h1>Saved searches</h1>
      <p className="muted">
        Each saved search has an RSS/Atom feed you can add to any feed reader.
      </p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <label>
          Name
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Query
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <label>
          City
          <input value={city} onChange={(e) => setCity(e.target.value)} />
        </label>
        <button type="submit">Create</button>
      </form>
      <ul className="saved">
        {(list.data ?? []).map((s) => (
          <li key={s.id}>
            <strong>{s.name}</strong> {s.query && `“${s.query}”`} · <a href={s.feed_url}>RSS</a> ·{" "}
            <a href={s.atom_url}>Atom</a>{" "}
            <button type="button" className="secondary" onClick={() => remove.mutate(s.id)}>
              Delete
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
