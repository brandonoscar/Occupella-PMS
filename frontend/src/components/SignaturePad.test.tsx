import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SignaturePad } from "./SignaturePad";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function ink() {
  const pen = { beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), clearRect: vi.fn() };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(pen as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,iVBORw0KGgo=");
  vi.spyOn(HTMLCanvasElement.prototype, "getBoundingClientRect").mockReturnValue(
    { left: 0, top: 0, width: 300, height: 100 } as DOMRect,
  );
  return pen;
}

describe("SignaturePad", () => {
  it("draws where the pointer goes and gives back the drawing as a PNG", () => {
    const pen = ink();
    const changed = vi.fn();
    render(<SignaturePad onChange={changed} />);
    const pad = screen.getByTestId("signature-pad");

    fireEvent.pointerDown(pad, { clientX: 10, clientY: 20, pointerId: 1 });
    fireEvent.pointerMove(pad, { clientX: 30, clientY: 40, pointerId: 1 });
    fireEvent.pointerUp(pad, { pointerId: 1 });

    // The box is drawn at half its size: a point lands at twice its place on the canvas.
    expect(pen.moveTo).toHaveBeenCalledWith(20, 40);
    expect(pen.lineTo).toHaveBeenCalledWith(60, 80);
    expect(pen.stroke).toHaveBeenCalled();
    expect(changed).toHaveBeenLastCalledWith("iVBORw0KGgo=");
    expect(pad.getAttribute("aria-labelledby")).toBe("signature-pad-label");
  });

  it("draws nothing without the pointer down, and clearing gives back nothing", () => {
    const pen = ink();
    const changed = vi.fn();
    render(<SignaturePad onChange={changed} />);
    const pad = screen.getByTestId("signature-pad");

    fireEvent.pointerMove(pad, { clientX: 30, clientY: 40 });
    fireEvent.pointerUp(pad);
    fireEvent.click(screen.getByRole("button", { name: "Clear the drawing" }));

    expect(pen.lineTo).not.toHaveBeenCalled();
    expect(pen.clearRect).toHaveBeenCalledWith(0, 0, 600, 200);
    expect(changed.mock.calls).toEqual([[null]]);
  });

  it("does nothing where the browser can't draw", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const changed = vi.fn();
    render(<SignaturePad onChange={changed} />);
    const pad = screen.getByTestId("signature-pad");

    fireEvent.pointerDown(pad, { clientX: 10, clientY: 20 });
    fireEvent.pointerUp(pad);

    expect(changed).not.toHaveBeenCalled();
  });
});
