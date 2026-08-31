import type { SmartMaskCandidate } from "@/lib/types";

export type MaskMode = "remove" | "add";
export type MaskTool = "smart" | "brush" | "erase";
export type MaskLayers = { smart: HTMLCanvasElement; include: HTMLCanvasElement; exclude: HTMLCanvasElement };
export type MaskSnapshot = { smart: string; include: string; exclude: string; selected: string[] };

export function makeCanvas(width: number, height: number) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

export const canvasDataUrl = (canvas: HTMLCanvasElement) => canvas.toDataURL("image/png");
export const clearCanvas = (canvas: HTMLCanvasElement) => canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);

export function forEachMaskRun(candidate: SmartMaskCandidate, visit: (start: number, end: number) => void) {
  let offset = 0;
  let selected = false;
  for (const count of candidate.rle) {
    const end = offset + Math.max(0, count | 0);
    if (selected && end > offset) visit(offset, end);
    offset = end;
    selected = !selected;
  }
}
