"use client";
import { useEffect } from "react";

import { api } from "@/lib/api";







import type { MaskMode } from "@/lib/inpaint/mask";
import { useAsyncScope } from "@/lib/editor/async-scope";
import { useInpaintState } from "./useInpaintState";
import { useInpaintMask } from "./useInpaintMask";
import { useSmartSelection } from "./useSmartSelection";
import { useInpaintTask } from "./useInpaintTask";
import { PURE_ERASERS, type InpaintDialogProps } from "./types";
export function useInpaintSession(props: InpaintDialogProps) {
  const { open, srcUrl, target } = props;
  const session = useInpaintState(); const { state, store, actions } = session;
  const { mode, prompt, brush, tool, hasMask, canUndo, scanBusy, pointBusy, smartMessage, scanCandidates, selectedCandidateIds, advancedOpen, removeGrow, addGrow, removeFeather, addFeather, seedText, nCount, removeModel, addModel, inpaintProvider, inpaintConfigLoaded } = state;
  const { setMode, setPrompt, setBrush, setTool, setCanUndo, setSmartMessage, setAdvancedOpen, setRemoveGrow, setAddGrow, setRemoveFeather, setAddFeather, setSeedText, setNCount, setRemoveModel, setAddModel, setInpaintProvider, setInpaintConfigLoaded } = actions;
  const mask = useInpaintMask(session); const smart = useSmartSelection(session, mask, target);
  const flow = useInpaintTask(props, store, mask.actions.exportMask);
  const { submit, redraw, reroll, applySelected, cancelRunning, handleOpenChange, setSelected } = flow.actions;
  const { candidates, selected, partialNote } = flow.state;
  const task = flow.state.phase === "running" ? { iid: flow.state.id, stage: flow.state.stage } : null;
  const applying = flow.state.phase === "applying", submitting = flow.state.phase === "submitting";
  const { boxRef, canvasRef, candidateCanvasRef, cursorRef, modeRef, scanCandidatesRef, undoStacksRef } = mask.refs;
  const { undo, clearMask, onMove, onUp, recompose, drawCandidateOverlay } = mask.actions;
  const { startObjectScan } = smart;
  const smartBusy = scanBusy || pointBusy;
  const eraserRemove = inpaintConfigLoaded && mode === "remove" && inpaintProvider === "fal" && PURE_ERASERS.has(removeModel);
  const grow = mode === "remove" ? removeGrow : addGrow, feather = mode === "remove" ? removeFeather : addFeather;
  const setGrow = mode === "remove" ? setRemoveGrow : setAddGrow, setFeather = mode === "remove" ? setRemoveFeather : setAddFeather;
  function requestFrame(callback: () => void) { mask.actions.invalidate(); mask.actions.frame(callback); }
  function changeMode(next: MaskMode) {
    modeRef.current = next;
    setMode(next);
    setTool("smart");
    setSmartMessage(next === "add"
      ? "智能选区：点击地面、墙面或桌面，再用画笔收窄"
      : scanCandidatesRef.current.length
        ? `已识别 ${scanCandidatesRef.current.length} 个候选，可在图上点选多个物件`
        : scanBusy ? "正在后台识别物件；也可以直接点击图中物件优先识别" : "未识别到物件，请使用画笔涂抹");
    setCanUndo(undoStacksRef.current[next].length > 0);
    requestFrame(() => {
      recompose(next);
      drawCandidateOverlay();
    });
    if (next === "remove" && inpaintConfigLoaded && inpaintProvider === "fal" && PURE_ERASERS.has(removeModel)) {
      setNCount(1);
    }
  }
  function onDown(e: React.PointerEvent) { if (flow.isBusy()) return; mask.actions.onDown(e, p => { if (store.getSnapshot().mode === "remove") smart.toggleCandidateAt(p); else void smart.selectPointRegion(p); }); }
  function onImgLoad(e: React.SyntheticEvent<HTMLImageElement>) { mask.actions.onImgLoad(e); void smart.startObjectScan(); }
  const lifetime = useAsyncScope();
  useEffect(() => {
    const token = lifetime.token();
    api
      .getConfig()
      .then((c) => {
        if (!lifetime.valid(token)) return;
        const configuredRemove = c.inpaint_remove_model || "bria-eraser";
        const configuredProvider = c.inpaint_provider || "fal";
        setRemoveModel(configuredRemove);
        setAddModel(c.inpaint_add_model || "flux-fill");
        setInpaintProvider(configuredProvider);
        if (
          modeRef.current === "remove" &&
          configuredProvider === "fal" &&
          PURE_ERASERS.has(configuredRemove)
        ) {
          setNCount(1);
        }
      })
      .catch(() => {
        /* 拉不到配置就按默认展示，不阻塞 */
      })
      .finally(() => { if (lifetime.valid(token)) setInpaintConfigLoaded(true); });
  }, [lifetime, modeRef, setRemoveModel, setAddModel, setInpaintProvider, setNCount, setInpaintConfigLoaded]);
  const modeBtn = (active: boolean) =>
    `h-8 rounded-[8px] px-3 text-[12px] font-bold transition-colors ${active
      ? "bg-primary text-primary-foreground"
      : "border border-border bg-panel text-secondary-foreground hover:bg-accent"
    }`;
  const toolBtn =
    "h-8 rounded-[8px] border border-border bg-panel px-2.5 text-[12px] font-semibold text-secondary-foreground hover:bg-accent disabled:opacity-40";


  return {
    state: { candidates, partialNote, selected, applying, nCount, tool, brush, task, smartMessage, mode, selectedCandidateIds, scanCandidates, canUndo, hasMask, advancedOpen, prompt, addModel, submitting, seedText },
    actions: { handleOpenChange, setSelected, redraw, reroll, applySelected, onDown, onMove, onUp, onImgLoad, cancelRunning, startObjectScan, changeMode, setTool, setSmartMessage, setBrush, undo, clearMask, setNCount, setAdvancedOpen, setPrompt, submit, setSeedText, setGrow, setFeather },
    canvas: { boxRef, cursorRef, candidateCanvasRef, canvasRef },
    display: { toolBtn, smartBusy, modeBtn, eraserRemove, grow, feather },
    input: { open, srcUrl }
  };
}