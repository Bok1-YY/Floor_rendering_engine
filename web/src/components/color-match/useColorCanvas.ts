"use client";
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { api } from '@/lib/api';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { DEFAULT_ADJUSTMENTS } from '@/lib/color-match/config';
import type { ColorSession } from './useColorMatchState';

export function useColorCanvas(store: ColorSession['store'], sourceUrl: string) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const srcImgRef = useRef<HTMLImageElement | null>(null);
  const fullPreviewRef = useRef<HTMLImageElement | null>(null);
  const autoPreviewRef = useRef<HTMLImageElement | null>(null);
  const autoAdjustmentsRef = useRef({ ...DEFAULT_ADJUSTMENTS });
  const scope = useAsyncScope();
  const redraw = useCallback(() => {
    const canvas = canvasRef.current, full = fullPreviewRef.current, src = srcImgRef.current;
    if (!canvas || !full) return;
    if (canvas.width !== full.naturalWidth || canvas.height !== full.naturalHeight) {
      canvas.width = full.naturalWidth; canvas.height = full.naturalHeight;
    }
    const ctx = canvas.getContext('2d'); if (!ctx) return;
    const state = store.getSnapshot();
    ctx.globalAlpha = 1;
    if (src?.complete && src.naturalWidth > 0) {
      ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
      ctx.globalAlpha = state.adjustmentMode === 'auto' ? state.strength : 1;
    }
    ctx.drawImage(full, 0, 0, canvas.width, canvas.height); ctx.globalAlpha = 1;
  }, [store]);
  useEffect(() => {
    scope.invalidate();
    void scope.image(api.imgUrl(sourceUrl)).then(image => { if (image) { srcImgRef.current = image; redraw(); } });
    return () => { scope.invalidate(); srcImgRef.current = fullPreviewRef.current = autoPreviewRef.current = null; };
  }, [scope, sourceUrl, redraw]);
  return useMemo(() => ({ canvasRef, srcImgRef, fullPreviewRef, autoPreviewRef, autoAdjustmentsRef, redraw }), [redraw]);
}
export type ColorCanvas = ReturnType<typeof useColorCanvas>;
