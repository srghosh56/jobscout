// Typed API client generated from the backend's OpenAPI schema (see `make openapi`).
// `paths` and `components` are TypeScript types produced by openapi-typescript, so a
// wrong URL, a missing field or a wrong type is a compile error, not a runtime bug.
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type PostingSummary = Schemas["PostingSummary"];
export type PostingDetail = Schemas["PostingDetail"];
export type SearchFilters = Schemas["SearchFilters"];
export type CvMatchResponse = Schemas["CvMatchResponse"];
export type SavedSearch = Schemas["SavedSearchOut"];

const KEY_STORAGE = "jobscout.apiKey";

function readKey(): string | null {
  try {
    return localStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

function writeKey(key: string | null): void {
  try {
    if (key) localStorage.setItem(KEY_STORAGE, key);
    else localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage unavailable (private mode): keep the key in memory only */
  }
}

let memoryKey: string | null = null;

/** Returns a stored API key or fetches a rate-limited demo key once. */
export async function getApiKey(): Promise<string> {
  const existing = memoryKey ?? readKey();
  if (existing) return existing;
  const res = await fetch("/api/demo-key", { method: "POST" });
  if (!res.ok) throw new Error(`Could not get a demo key (${res.status})`);
  const body = (await res.json()) as { api_key: string };
  memoryKey = body.api_key;
  writeKey(body.api_key);
  return body.api_key;
}

export function forgetApiKey(): void {
  memoryKey = null;
  writeKey(null);
}

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    if (!request.url.includes("/api/demo-key")) {
      request.headers.set("X-API-Key", await getApiKey());
    }
    return request;
  },
  async onResponse({ response }) {
    if (response.status === 401) forgetApiKey(); // expired demo key: get a new one next time
    return response;
  },
};

// An absolute base URL keeps Request() happy outside the browser (tests, SSR).
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  fetch: (req) => globalThis.fetch(req), // late-bound so tests can stub fetch
});
api.use(authMiddleware);

/** The insights endpoint returns a free-form JSON document; this mirrors jobscout/insights.py. */
export interface Rates {
  n: number;
  share_english_friendly: number;
  share_salary_stated: number;
  share_visa_or_relocation: number;
}
export interface Insights {
  generated_at: string;
  coverage: {
    active_postings: number;
    extracted: number;
    llm_extracted: number;
    rules_extracted: number;
    last_ingested: string | null;
    last_checked: string | null;
  };
  by_city: (Rates & { city: string })[];
  by_job_family: (Rates & { job_family: string })[];
  top_skills: Record<string, { skill: string; n: number }[]>;
  weekly: {
    week: string;
    new_postings: number;
    n: number;
    share_english_friendly: number | null;
    share_salary_stated: number | null;
    share_visa_or_relocation: number | null;
  }[];
  min_cell_size: number;
  methodology: string;
}
