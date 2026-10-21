import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Button } from "./Button";
import { Form } from "./Form";

afterEach(cleanup);

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("Form and Button", () => {
  it("shows the submit button busy while the save runs, and can't send it twice", async () => {
    const save = deferred();
    const submit = vi.fn(() => save.promise);
    render(
      <Form onSubmit={(event) => { event.preventDefault(); return submit(); }}>
        <Button type="submit">Record payment</Button>
        <Button type="button" variant="secondary">Cancel</Button>
      </Form>,
    );
    const button = screen.getByRole("button", { name: "Record payment" }) as HTMLButtonElement;
    fireEvent.click(button);
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    // Only the submit button waits.
    expect((screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.submit(button.closest("form")!);
    expect(submit).toHaveBeenCalledTimes(1);
    save.resolve();
    await vi.waitFor(() => expect(button.disabled).toBe(false));
    expect(button.getAttribute("aria-busy")).toBeNull();
  });

  it("leaves a form whose handler returns nothing as it was", () => {
    render(<Form onSubmit={(event) => event.preventDefault()}><Button type="submit">Go</Button></Form>);
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect((screen.getByRole("button", { name: "Go" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows a button busy while the promise its click returned runs", async () => {
    const send = deferred();
    render(<Button onClick={() => send.promise}>Send invite</Button>);
    const button = screen.getByRole("button", { name: "Send invite" }) as HTMLButtonElement;
    fireEvent.click(button);
    expect(button.disabled).toBe(true);
    send.resolve();
    await vi.waitFor(() => expect(button.disabled).toBe(false));
  });

  it("takes busy from its caller too", () => {
    render(<Button busy>Save</Button>);
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
