export function createCanvas(width: number, height: number) {
  const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height; return canvas;
}

export function normalizedPoint(event: { clientX: number; clientY: number }, element: HTMLElement) {
  const box = element.getBoundingClientRect();
  if (box.width <= 0 || box.height <= 0) return null;
  return {
    x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
    y: Math.max(0, Math.min(1, (event.clientY - box.top) / box.height))
  };
}

/** Preserve inpaint's alpha>0 rule; colour-mask export retains its separate alpha semantics. */
export function exportBinaryMask(canvas: HTMLCanvasElement | null): string | null {
  const ctx = canvas?.getContext('2d');
  if (!canvas || !ctx || !canvas.width) return null;
  const src = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const out = new ImageData(canvas.width, canvas.height);
  for (let i = 0;i < src.data.length;i += 4) {
    const v = src.data[i + 3] > 0 ? 255 : 0;
    out.data[i] = out.data[i + 1] = out.data[i + 2] = v; out.data[i + 3] = 255;
  }
  const buffer = createCanvas(canvas.width, canvas.height);
  buffer.getContext('2d')!.putImageData(out, 0, 0);
  return buffer.toDataURL('image/png').split(',', 2)[1] || null;
}
