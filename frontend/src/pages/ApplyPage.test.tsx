import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApplyPage } from "./ApplyPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const LISTING = { pmc: "Synthetic PMC", property: "Property 1", unit: "Unit 1", monthly_rent: "1500.00" };

function server(apply: Response) {
  const fetch = vi.fn(async (path: string, _init?: RequestInit) =>
    path === "/api/apply/view" ? new Response(JSON.stringify(LISTING)) : apply,
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

// What each applicant fills in besides their landlord, employer and proof of income: synthetic
// people and phones (555-0100 to 555-0199).
function fill(n: number, name: string, email: string) {
  const at = (label: string) => screen.getAllByLabelText(label)[n] as HTMLElement;
  fireEvent.change(at("Full name"), { target: { value: name } });
  fireEvent.change(at("Email"), { target: { value: email } });
  fireEvent.change(at("Phone"), { target: { value: `555-555-010${n}` } });
  fireEvent.change(at("Street address"), { target: { value: `${n + 1} Sample Street` } });
  fireEvent.change(at("City"), { target: { value: "Sampleton" } });
  fireEvent.change(at("State"), { target: { value: "ST" } });
  fireEvent.change(at("ZIP code"), { target: { value: "00001" } });
  fireEvent.change(at("Emergency contact's name"), { target: { value: `Contact ${n + 1}` } });
  fireEvent.change(at("Emergency contact's phone"), { target: { value: "555-555-0160" } });
}

function details(n: number) {
  return {
    phone: `555-555-010${n}`,
    address: { line_1: `${n + 1} Sample Street`, city: "Sampleton", state: "ST", postal_code: "00001" },
    emergency_contact: { name: `Contact ${n + 1}`, phone: "555-555-0160" },
  };
}

describe("ApplyPage", () => {
  it("shows what the link is for, sends the application and gives the status link", async () => {
    window.history.replaceState(null, "", "/apply#link-token");
    const fetch = server(
      new Response(JSON.stringify({ status_url: "http://x/application#status" }), { status: 201 }),
    );
    render(<ApplyPage />);

    expect(await screen.findByText("Apply for Property 1, Unit 1")).toBeTruthy();
    expect(screen.getByText(/offers it at \$1,500.00 a month/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Move-in day"), { target: { value: "2026-12-01" } });
    fireEvent.change(screen.getByLabelText("Monthly income of everyone applying, together"), {
      target: { value: "4500" },
    });
    fill(0, "Applicant A", "a@example.com");
    fireEvent.change(screen.getByLabelText("Previous landlord's name"), { target: { value: "Landlord 1" } });
    fireEvent.change(screen.getByLabelText("Previous landlord's phone"), { target: { value: "555-555-0150" } });
    fireEvent.change(screen.getByLabelText("Rented from"), { target: { value: "2023-01-01" } });
    fireEvent.change(screen.getByLabelText("Rented to"), { target: { value: "2025-12-31" } });
    fireEvent.change(screen.getByLabelText("Employer, if you have one"), { target: { value: "Employer 1" } });
    fireEvent.change(screen.getByLabelText(/^Proof of income/), {
      target: { files: [new File(["%PDF-1.4 synthetic"], "stub.pdf", { type: "application/pdf" })] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    fireEvent.click(screen.getByRole("button", { name: "Add someone else applying" }));
    fill(1, "Applicant B", "b@example.com");
    // The household: someone else living there, a pet and an assistance animal, and a car.
    fireEvent.click(screen.getByRole("button", { name: "Add someone" }));
    fireEvent.change(screen.getByLabelText("Name of person 1"), { target: { value: "Occupant 1" } });
    fireEvent.click(screen.getByRole("button", { name: "Add an animal" }));
    fireEvent.click(screen.getByRole("button", { name: "Add an animal" }));
    const names = screen.getAllByLabelText("Animal's name");
    const kinds = screen.getAllByLabelText("Kind (dog, cat...)");
    fireEvent.change(names[0] as HTMLElement, { target: { value: "Pet 1" } });
    fireEvent.change(kinds[0] as HTMLElement, { target: { value: "Cat" } });
    fireEvent.change(screen.getAllByLabelText("Weight in pounds (if known)")[0] as HTMLElement, { target: { value: "9.5" } });
    fireEvent.change(names[1] as HTMLElement, { target: { value: "Pet 2" } });
    fireEvent.change(kinds[1] as HTMLElement, { target: { value: "Dog" } });
    fireEvent.click(screen.getAllByLabelText(/An assistance animal/)[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Add a vehicle" }));
    fireEvent.change(screen.getByLabelText("Make"), { target: { value: "Make 1" } });
    fireEvent.change(screen.getByLabelText("License plate"), { target: { value: "SAMPLE1" } });
    fireEvent.click(screen.getByRole("button", { name: "Send application" }));

    expect((await screen.findByTestId("status-url")).textContent).toBe("http://x/application#status");
    const sent = JSON.parse(String(fetch.mock.calls[1]?.[1]?.body));
    expect(sent).toEqual({
      token: "link-token",
      move_in_on: "2026-12-01",
      monthly_income: "4500",
      applicants: [
        {
          name: "Applicant A",
          email: "a@example.com",
          details: {
            ...details(0),
            previous_landlord: { name: "Landlord 1", phone: "555-555-0150", from_on: "2023-01-01", to_on: "2025-12-31" },
            employer: "Employer 1",
            income_proof: { content_type: "application/pdf", content_base64: btoa("%PDF-1.4 synthetic") },
          },
        },
        { name: "Applicant B", email: "b@example.com", details: details(1) },
      ],
      household: {
        occupants: ["Occupant 1"],
        pets: [
          { name: "Pet 1", kind: "Cat", weight_lbs: "9.5", assistance_animal: false },
          { name: "Pet 2", kind: "Dog", assistance_animal: true },
        ],
        vehicles: [{ make: "Make 1", plate: "SAMPLE1" }],
      },
    });
  });

  it("asks nothing fair housing protects, and no Social Security number or birth date", async () => {
    window.history.replaceState(null, "", "/apply#link-token");
    server(new Response("{}", { status: 201 }));
    render(<ApplyPage />);
    await screen.findByText("Apply for Property 1, Unit 1");
    fireEvent.click(screen.getByRole("button", { name: "Add an animal" }));
    const asked = Array.from(document.querySelectorAll("label")).map((label) => label.textContent ?? "").join(" | ");
    for (const never of [/social security/i, /\bSSN\b/, /birth/i, /\bage\b/i, /race/i, /religio/i, /national/i,
      /sex\b|gender/i, /familial|children/i, /disabilit/i, /marital/i, /citizen/i]) {
      expect(asked).not.toMatch(never);
    }
  });

  it("refuses a proof of income that isn't a PDF, a JPEG or a PNG", async () => {
    window.history.replaceState(null, "", "/apply#link-token");
    server(new Response("{}", { status: 201 }));
    render(<ApplyPage />);
    await screen.findByText("Apply for Property 1, Unit 1");
    fireEvent.change(screen.getByLabelText(/^Proof of income/), {
      target: { files: [new File(["hello"], "notes.txt", { type: "text/plain" })] },
    });
    expect((await screen.findByRole("alert")).textContent).toBe("A proof of income is a PDF, a JPEG or a PNG.");
  });

  it("shows every applicant the company's screening criteria", async () => {
    window.history.replaceState(null, "", "/apply#link-token");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      pmc: "Synthetic PMC", property: "Property 1", unit: "Unit 1", monthly_rent: "1500.00",
      criteria: "Household income of 2.5 times the rent.",
    }))));
    render(<ApplyPage />);

    const criteria = await screen.findByTestId("screening-criteria");
    expect(criteria.textContent).toBe("How Synthetic PMC screens every applicantHousehold income of 2.5 times the rent.");
  });

  it("applies through a listing from the public page", async () => {
    window.history.replaceState(null, "", "/apply?listing=v1");
    const fetch = server(
      new Response(JSON.stringify({ status_url: "http://x/application#status" }), { status: 201 }),
    );
    render(<ApplyPage />);

    expect(await screen.findByText("Apply for Property 1, Unit 1")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Move-in day"), { target: { value: "2026-12-01" } });
    fireEvent.change(screen.getByLabelText("Monthly income of everyone applying, together"), {
      target: { value: "4500" },
    });
    fill(0, "Applicant A", "a@example.com");
    fireEvent.click(screen.getByRole("button", { name: "Send application" }));

    expect(await screen.findByTestId("status-url")).toBeTruthy();
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({ listing: "v1" });
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toMatchObject({ listing: "v1" });
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body)).token).toBeUndefined();
  });

  it("says so when the link is closed", async () => {
    window.history.replaceState(null, "", "/apply#old");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "this application link is closed or doesn't exist" }), {
          status: 404,
        }),
      ),
    );
    render(<ApplyPage />);

    expect((await screen.findByRole("alert")).textContent).toBe(
      "this application link is closed or doesn't exist",
    );
  });
});
