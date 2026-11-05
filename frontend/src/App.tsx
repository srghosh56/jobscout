import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { AssistantPage } from "./pages/AssistantPage";
import { CvMatchPage } from "./pages/CvMatchPage";
import { InsightsPage } from "./pages/InsightsPage";
import { PostingPage } from "./pages/PostingPage";
import { SavedSearchesPage } from "./pages/SavedSearchesPage";
import { SearchPage } from "./pages/SearchPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, retry: 1 } },
});

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header>
        <strong className="logo">JobScout</strong>
        <nav aria-label="Main">
          <NavLink to="/">Search</NavLink>
          <NavLink to="/assistant">Assistant</NavLink>
          <NavLink to="/cv">CV match</NavLink>
          <NavLink to="/insights">Insights</NavLink>
          <NavLink to="/saved">Saved searches</NavLink>
        </nav>
      </header>
      <main id="main">{children}</main>
      <footer>
        Jobs from <a href="https://www.arbeitnow.com">Arbeitnow.com</a> and the{" "}
        <a href="https://www.arbeitsagentur.de/jobsuche/">Bundesagentur für Arbeit Jobsuche</a>.
        Blue Card information is indicative only, not legal advice.
      </footer>
    </>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Shell>
          <Routes>
            <Route path="/" element={<SearchPage />} />
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="/cv" element={<CvMatchPage />} />
            <Route path="/insights" element={<InsightsPage />} />
            <Route path="/saved" element={<SavedSearchesPage />} />
            <Route path="/postings/:id" element={<PostingPage />} />
          </Routes>
        </Shell>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
