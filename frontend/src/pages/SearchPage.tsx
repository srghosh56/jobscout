import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type SearchFilters } from "../api/client";
import { Filters } from "../components/Filters";
import { PostingCard } from "../components/PostingCard";

export function SearchPage() {
  // useState holds component state; calling the setter re-renders the component.
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<SearchFilters>({});
  const [saved, setSaved] = useState<string | null>(null);

  // TanStack Query caches the result per queryKey and refetches when the key changes.
  const { data, isFetching, error } = useQuery({
    queryKey: ["search", query, filters],
    queryFn: async () => {
      const { data, error } = await api.POST("/api/search", {
        body: { query, filters, top_k: 20, mode: "hybrid_rerank" },
      });
      if (error) throw new Error("Search failed");
      return data;
    },
  });

  async function saveSearch() {
    const name = query || filters.city || "My search";
    const { data } = await api.POST("/api/saved-searches", { body: { name, query, filters } });
    if (data) setSaved(data.feed_url);
  }

  return (
    <section>
      <h1>Search jobs</h1>
      <form
        className="searchbar"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(draft);
        }}
      >
        <label className="sr-only" htmlFor="q">
          Search
        </label>
        <input
          id="q"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g. machine learning RAG"
        />
        <button type="submit">Search</button>
        <button type="button" className="secondary" onClick={saveSearch}>
          Save as feed
        </button>
      </form>
      {saved && (
        <p role="status">
          Saved. RSS feed: <a href={saved}>{saved}</a>
        </p>
      )}
      <div className="layout-2">
        <Filters value={filters} onChange={setFilters} />
        <div aria-live="polite" aria-busy={isFetching}>
          {error && <p role="alert">{(error as Error).message}</p>}
          {data && (
            <p className="muted">
              {data.results.length} results ({data.mode}, {data.took_ms} ms)
            </p>
          )}
          {data?.results.map((p) => (
            <PostingCard key={p.id} p={p} />
          ))}
        </div>
      </div>
    </section>
  );
}
