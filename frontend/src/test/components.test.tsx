import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import type { SearchFilters } from "../api/client";
import { AnswerText } from "../components/CitationChip";
import { Filters } from "../components/Filters";

function Harness({ onChange }: { onChange: (f: SearchFilters) => void }) {
  const [f, setF] = useState<SearchFilters>({});
  return (
    <Filters
      value={f}
      onChange={(n) => {
        setF(n);
        onChange(n);
      }}
    />
  );
}

test("filters emit typed values for every kind of control", async () => {
  const seen: SearchFilters[] = [];
  render(<Harness onChange={(f) => seen.push(f)} />);
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("City"), "Berlin");
  await user.type(screen.getByLabelText("Max years of experience"), "3");
  await user.selectOptions(screen.getByLabelText("English is enough"), "true");
  await user.click(screen.getByLabelText("remote"));
  await user.click(screen.getByLabelText("meets standard"));
  const last = seen.at(-1)!;
  expect(last).toMatchObject({
    city: "Berlin",
    max_years: 3,
    english_sufficient: true,
    remote_policy: ["remote"],
    bluecard_status: ["meets_standard"],
  });
  await user.click(screen.getByLabelText("remote"));
  expect(seen.at(-1)!.remote_policy).toBeNull();
  await user.selectOptions(screen.getByLabelText("English is enough"), "");
  expect(seen.at(-1)!.english_sufficient).toBeNull();
});

test("citation chips link to postings and mark unverified IDs", () => {
  render(
    <MemoryRouter>
      <AnswerText
        text="Good fit [an-0000000001] and [ba-0000000004, an-0000000002]."
        citations={["an-0000000001", "ba-0000000004"]}
      />
    </MemoryRouter>,
  );
  expect(screen.getByRole("link", { name: "Open posting an-0000000001" })).toHaveAttribute(
    "href",
    "/postings/an-0000000001",
  );
  expect(screen.getByRole("link", { name: "Open posting ba-0000000004" })).toBeInTheDocument();
  expect(screen.getByTitle("Not verified")).toHaveTextContent("an-0000000002");
  expect(screen.getByText(/Good fit/)).toBeInTheDocument();
});
