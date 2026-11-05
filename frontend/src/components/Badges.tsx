import { BLUECARD_LABEL, ENGLISH_LABEL } from "../lib/format";

// A component is a function that takes "props" (its inputs) and returns markup.
export function BlueCardBadge({ status }: { status: string }) {
  return (
    <span className={`badge bc-${status}`} title="Indicative only, not legal advice">
      {BLUECARD_LABEL[status] ?? status}
    </span>
  );
}

export function EnglishBadge({ value }: { value: string }) {
  return <span className={`badge en-${value}`}>{ENGLISH_LABEL[value] ?? value}</span>;
}
