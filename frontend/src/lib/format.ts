export const BLUECARD_LABEL: Record<string, string> = {
  meets_standard: "Blue Card: meets standard",
  meets_reduced_only: "Blue Card: reduced threshold only",
  below: "Blue Card: below threshold",
  salary_not_stated: "Blue Card: salary not stated",
};

export const ENGLISH_LABEL: Record<string, string> = {
  yes: "English is enough",
  no: "German required",
  unclear: "Language unclear",
};

export function pct(x: number | null | undefined): string {
  return x == null ? "–" : `${Math.round(x * 1000) / 10}%`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "never";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
