"use client";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api";

import { clearCanvas } from "@/lib/inpaint/mask";
import type { MaskMode } from "@/lib/inpaint/mask";
import { useMemo } from "react";
import { useAsyncScope } from "@/lib/editor/async-scope";
import type { InpaintSessionState } from "./useInpaintState";
import type { InpaintMask } from "./useInpaintMask";
import { toTargetPayload, type InpaintTarget } from "./types";
export function useSmartSelection(session: InpaintSessionState, mask: InpaintMask, target: InpaintTarget) {
  const { state, store, actions } = session;
  const { setScanBusy, setPointBusy, setSmartMessage, setScanCandidates } = actions;
  const { canvasRef, scanCandidatesRef, scanSizeRef, ownerMapRef, selectedIdsRef, modeRef } = mask.refs;
  const { pushUndo, setSelectedIds, rebuildRemoveSmartLayer, drawCandidateOverlay, activeLayers, drawRleMask, recompose } = mask.actions;
  const scanAbortRef = useRef<AbortController | null>(null), scanStartedRef = useRef(false), smartRequestSeq = useRef(0);
  const pointBusyRef = useMemo(() => store.field("pointBusy"), [store]);
  const scanScope = useAsyncScope(), pointScope = useAsyncScope();
  useEffect(() => { pointScope.invalidate(); smartRequestSeq.current++; setPointBusy(false); }, [state.mode, pointScope, setPointBusy]);
  async function startObjectScan(force = false) {
    if (scanStartedRef.current && !force) return;
    scanStartedRef.current = true;
    scanAbortRef.current?.abort();
    const controller = new AbortController();
    scanAbortRef.current = controller;
    const release = scanScope.own(() => controller.abort());
    setScanBusy(true);
    setSmartMessage("正在后台识别物件，画笔仍可使用…");
    try {
      const result = await api.inpaintSegment({
        target: toTargetPayload(target),
        strategy: "scan_objects",
      }, controller.signal);
      if (controller.signal.aborted) return;
      const clickedCandidates = scanCandidatesRef.current.filter((candidate) => candidate.id.startsWith("point-object-"));
      const mergedCandidates = [...result.candidates, ...clickedCandidates];
      scanCandidatesRef.current = mergedCandidates;
      scanSizeRef.current = { width: result.width, height: result.height };
      setScanCandidates(mergedCandidates);
      if (modeRef.current === "remove" && !pointBusyRef.current) {
        setSmartMessage(mergedCandidates.length
          ? `已识别 ${mergedCandidates.length} 个候选，可在图上点选多个物件`
          : result.warnings[0] || "未识别到物件，请使用画笔涂抹");
      }
      mask.actions.queueOverlay();
    } catch (error) {
      if (!controller.signal.aborted) {
        setSmartMessage(`智能识别失败：${(error as Error).message}；仍可使用画笔`);
      }
    } finally {
      release();
      if (!controller.signal.aborted) setScanBusy(false);
    }
  }

  async function selectPointRegion(p: { x: number; y: number }, which: MaskMode = "add") {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (pointBusyRef.current) {
      setSmartMessage("AI 正在处理上一次点击，请稍等片刻");
      return;
    }
    pointScope.invalidate();
    const token = pointScope.token();
    const controller = new AbortController();
    const release = pointScope.own(() => controller.abort());
    const seq = ++smartRequestSeq.current;
    pointBusyRef.current = true;
    setPointBusy(true);
    setSmartMessage(which === "remove" ? "正在识别点击的物件…" : "正在识别点击位置…");
    try {
      const result = await api.inpaintSegment({
        target: toTargetPayload(target),
        strategy: "point",
        point: { x: p.x / canvas.width, y: p.y / canvas.height },
      }, controller.signal);
      if (!pointScope.valid(token)) return;
      if (seq !== smartRequestSeq.current || !result.candidates[0]) {
        if (seq === smartRequestSeq.current) {
          setSmartMessage(result.warnings[0] || "该位置未识别到区域，请换个位置或用画笔");
        }
        return;
      }
      if (which === "remove") {
        pushUndo("remove");
        const candidate = { ...result.candidates[0], id: `point-object-${seq}` };
        scanCandidatesRef.current = [...scanCandidatesRef.current, candidate];
        scanSizeRef.current = { width: result.width, height: result.height };
        setScanCandidates(scanCandidatesRef.current);
        const next = new Set(selectedIdsRef.current);
        next.add(candidate.id);
        setSelectedIds(next);
        rebuildRemoveSmartLayer();
        drawCandidateOverlay();
        setSmartMessage(`已按点击位置选中物件 · 置信度 ${Math.round(candidate.confidence * 100)}%，可继续多选或用画笔修正`);
      } else {
        pushUndo("add");
        const layers = activeLayers("add");
        if (!layers) return;
        clearCanvas(layers.smart);
        clearCanvas(layers.include);
        clearCanvas(layers.exclude);
        drawRleMask(layers.smart, result.candidates[0], result.width, result.height);
        recompose("add");
        setSmartMessage(`已识别目标区域 · 置信度 ${Math.round(result.candidates[0].confidence * 100)}%，可用画笔收窄或补充`);
      }
    } catch (error) {
      if (pointScope.valid(token) && seq === smartRequestSeq.current) {
        setSmartMessage(`点选识别失败：${(error as Error).message}；仍可使用画笔`);
      }
    } finally {
      release();
      if (pointScope.valid(token) && seq === smartRequestSeq.current) {
        pointBusyRef.current = false;
        setPointBusy(false);
      }
    }
  }

  function toggleCandidateAt(p: { x: number; y: number }) {
    const owner = ownerMapRef.current;
    const canvas = canvasRef.current;
    const { width, height } = scanSizeRef.current;
    if (!canvas) return;
    if (pointBusyRef.current) {
      setSmartMessage("AI 正在处理上一次点击，请稍等片刻");
      return;
    }
    if (!owner || !width || !height) {
      void selectPointRegion(p, "remove");
      return;
    }
    const x = Math.max(0, Math.min(width - 1, Math.floor(p.x / canvas.width * width)));
    const y = Math.max(0, Math.min(height - 1, Math.floor(p.y / canvas.height * height)));
    const candidateIndex = owner[y * width + x];
    const candidate = scanCandidatesRef.current[candidateIndex];
    if (!candidate) {
      void selectPointRegion(p, "remove");
      return;
    }
    pushUndo("remove");
    const next = new Set(selectedIdsRef.current);
    if (next.has(candidate.id)) next.delete(candidate.id);
    else next.add(candidate.id);
    setSelectedIds(next);
    rebuildRemoveSmartLayer();
    requestAnimationFrame(drawCandidateOverlay);
  }
  return { startObjectScan, selectPointRegion, toggleCandidateAt };
}
