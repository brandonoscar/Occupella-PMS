import { useRef } from "react";
import { Button } from "./Button";

// A box to draw a signature in with a finger, a pen or a mouse (the signing page, ADR 0008). It is
// optional: the name typed is the signature, and a drawing goes with it. `onChange` gets the PNG
// in base64 when a stroke ends, and null once cleared. The ink is the page's own text colour.
export function SignaturePad({ onChange }: { onChange: (png: string | null) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);

  function pen() {
    const box = canvas.current;
    const ink = box?.getContext("2d");
    if (!box || !ink) return null;
    ink.strokeStyle = getComputedStyle(box).color;
    ink.lineWidth = 3;
    ink.lineCap = "round";
    ink.lineJoin = "round";
    return { box, ink };
  }

  function at(event: React.PointerEvent<HTMLCanvasElement>, box: HTMLCanvasElement): [number, number] {
    const shown = box.getBoundingClientRect();
    return [
      ((event.clientX - shown.left) * box.width) / (shown.width || box.width),
      ((event.clientY - shown.top) * box.height) / (shown.height || box.height),
    ];
  }

  function down(event: React.PointerEvent<HTMLCanvasElement>) {
    const found = pen();
    if (!found) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drawing.current = true;
    found.ink.beginPath();
    found.ink.moveTo(...at(event, found.box));
  }

  function move(event: React.PointerEvent<HTMLCanvasElement>) {
    const found = pen();
    if (!drawing.current || !found) return;
    found.ink.lineTo(...at(event, found.box));
    found.ink.stroke();
  }

  function up() {
    if (!drawing.current || !canvas.current) return;
    drawing.current = false;
    onChange(canvas.current.toDataURL("image/png").split(",", 2)[1] ?? null);
  }

  function clear() {
    const found = pen();
    found?.ink.clearRect(0, 0, found.box.width, found.box.height);
    onChange(null);
  }

  return (
    <div className="flex flex-col gap-1.5 text-sm text-ink-muted">
      <span id="signature-pad-label">Draw your signature (optional)</span>
      <canvas ref={canvas} width={600} height={200} aria-labelledby="signature-pad-label"
        data-testid="signature-pad"
        className="h-32 w-full touch-none rounded-sm border border-line-strong bg-sunken text-ink"
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={up} />
      <div>
        <Button type="button" variant="secondary" onClick={clear}>Clear the drawing</Button>
      </div>
    </div>
  );
}
