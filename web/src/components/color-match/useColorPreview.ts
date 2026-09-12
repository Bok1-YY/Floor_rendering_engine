"use client";
import { useCallback } from 'react';
import { api } from '@/lib/api';
import { useAsyncScope } from '@/lib/editor/async-scope';
import { algorithmLabel, scaleAutoAdjustments, type AdjustmentMode } from '@/lib/color-match/config';
import type { ColorMatchRect, ColorMatchAdjustments } from '@/lib/types';
import { toast } from 'sonner';
import type { ColorSession } from './useColorMatchState';
import type { ColorCanvas } from './useColorCanvas';

export function useColorPreview(session: ColorSession, canvas: ColorCanvas, imageRel: string) {
  const scope = useAsyncScope();
  const { store } = session;
  const invalidatePreview = useCallback(() => { scope.invalidate(); store.set('ready', false); }, [scope, store]);
  const schedulePreview = useCallback((rect: ColorMatchRect, refPath: string, adjustments: ColorMatchAdjustments,
    mode: AdjustmentMode, delay = 180, includeAnalysis = false) => {
    invalidatePreview();
    store.set('previewEngineError', '');
    const requested = store.getSnapshot();
    if (!refPath || rect.w < .02 || rect.h < .02 || (requested.scope === 'floor_mask' && !requested.maskB64)) {
      store.set('previewing', false); store.set('analyzing', false); return;
    }
    const token = scope.token();
    store.set('previewing', true);
    if (includeAnalysis) store.set('analyzing', true);
    const timer = setTimeout(async () => {
      releaseTimer();
      if (!scope.valid(token)) return;
      const controller = new AbortController();
      const release = scope.own(() => controller.abort());
      try {
        const result = await api.colorMatchPreview({
          image_rel: imageRel, ref_path: refPath, rect, strength: 1,
          adjustments, adjustment_mode: mode, include_analysis: includeAnalysis, scope: requested.scope,
          mask_b64: requested.maskB64, mask_feather: requested.maskFeather,
          algorithm: requested.algorithm, illumination_mode: requested.illuminationMode
        }, controller.signal);
        if (!scope.valid(token)) return;
        const image = await scope.image(result.preview);
        if (!scope.valid(token)) return;
        if (!image) throw new Error('预览图片读取失败');
        const current = store.getSnapshot();
        const actualAlgorithm = result.quality_report?.algorithm ?? current.appliedAlgorithm ?? requested.algorithm;
        const actualIllumination = result.quality_report?.applied_illumination_mode ?? current.appliedIlluminationMode;
        canvas.autoAdjustmentsRef.current = result.auto_adjustments;
        canvas.fullPreviewRef.current = image;
        if (mode === 'auto') canvas.autoPreviewRef.current = image;
        if (includeAnalysis) { store.set('analysis', result.analysis ?? null); store.set('quality', result.quality_report ?? null); store.set('analyzing', false); }
        if (mode === 'auto') store.set('adjustments', scaleAutoAdjustments(result.auto_adjustments, current.strength));
        store.set('appliedAlgorithm', actualAlgorithm); store.set('appliedIlluminationMode', actualIllumination);
        if (result.quality_report) store.set('appliedFallbackReason', result.quality_report.fallback_reason ?? '');
        store.set('adjustmentMode', mode); store.set('hasPreview', true); store.set('ready', true);
        store.set('previewing', false); store.set('previewEngineError', '');
        canvas.redraw();
        if (requested.appliedAlgorithm !== null && (requested.appliedAlgorithm !== actualAlgorithm
          || requested.appliedIlluminationMode !== actualIllumination || requested.algorithm !== requested.appliedAlgorithm
          || requested.illuminationMode !== requested.appliedIlluminationMode)) {
          if (requested.algorithm !== actualAlgorithm) toast.warning(`精细 2.0 已回退，当前实际使用${algorithmLabel(actualAlgorithm)}`);
          else if (requested.illuminationMode !== actualIllumination) toast.warning(`${algorithmLabel(actualAlgorithm)}预览已更新，但空间光照校正未能应用`);
          else toast.success(`${algorithmLabel(actualAlgorithm)}预览已更新`);
        }
      } catch (error) {
        if (!scope.valid(token) || controller.signal.aborted) return;
        store.set('previewing', false); store.set('previewEngineError', (error as Error).message);
        if (includeAnalysis) store.set('analyzing', false);
        toast.error(`预览失败：${(error as Error).message}`);
      } finally { release(); }
    }, delay);
    const releaseTimer = scope.own(() => clearTimeout(timer));
  }, [scope, store, canvas, imageRel, invalidatePreview]);
  return { schedulePreview, invalidatePreview };
}
