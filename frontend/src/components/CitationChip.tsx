import { Link } from "react-router-dom";

const CITE = /\[((?:ba|an)-[0-9a-f]{10}(?:[,;\s]+(?:ba|an)-[0-9a-f]{10})*)\]/g;

export function CitationChip({ id, valid = true }: { id: string; valid?: boolean }) {
  return valid ? (
    <Link className="cite" to={`/postings/${id}`} aria-label={`Open posting ${id}`}>
      {id}
    </Link>
  ) : (
    <span className="cite invalid" title="Not verified">
      {id}
    </span>
  );
}

/** Renders answer text, turning [posting_id] references into clickable chips. */
export function AnswerText({ text, citations }: { text: string; citations: string[] }) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(CITE)) {
    parts.push(text.slice(last, m.index));
    for (const id of m[1]!.split(/[,;\s]+/)) {
      parts.push(
        <CitationChip
          key={`${m.index}-${id}`}
          id={id}
          valid={citations.length === 0 || citations.includes(id)}
        />,
      );
    }
    last = (m.index ?? 0) + m[0].length;
  }
  parts.push(text.slice(last));
  return <div className="answer">{parts}</div>;
}
