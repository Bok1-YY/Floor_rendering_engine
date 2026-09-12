"use client";

import type { SmartMaskCandidate } from "@/lib/types";

import type { MaskMode, MaskTool } from "@/lib/inpaint/mask";
import { useMemo } from "react";
import { useSessionStore } from "@/lib/editor/session-store";
export function useInpaintState() {
  const context = useSessionStore(() => ({    
mode: ("remove") as MaskMode,
    prompt: "",
    brush: 36,
    tool: ("smart") as MaskTool,
    hasMask: false,
    canUndo: false,
    scanBusy: false,
    pointBusy: false,
    smartMessage: "正在后台识别物件…",
    scanCandidates: ([]) as SmartMaskCandidate[],
    selectedCandidateIds: ([]) as string[],
    advancedOpen: false,
    removeGrow: 8,
    addGrow: 0,
    removeFeather: 0.01,
    addFeather: 0.005,
    seedText: "",
    nCount: 3,
    removeModel: "bria-eraser",
    addModel: "flux-fill",
    inpaintProvider: "fal",
    inpaintConfigLoaded: false  
}));
  const { store } = context;
  const actions = useMemo(() => ({    
setMode: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["mode"]>) => store.set("mode", value),
    setPrompt: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["prompt"]>) => store.set("prompt", value),
    setBrush: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["brush"]>) => store.set("brush", value),
    setTool: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["tool"]>) => store.set("tool", value),
    setHasMask: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["hasMask"]>) => store.set("hasMask", value),
    setCanUndo: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["canUndo"]>) => store.set("canUndo", value),
    setScanBusy: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["scanBusy"]>) => store.set("scanBusy", value),
    setPointBusy: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["pointBusy"]>) => store.set("pointBusy", value),
    setSmartMessage: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["smartMessage"]>) => store.set("smartMessage", value),
    setScanCandidates: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["scanCandidates"]>) => store.set("scanCandidates", value),
    setSelectedCandidateIds: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["selectedCandidateIds"]>) => store.set("selectedCandidateIds", value),
    setAdvancedOpen: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["advancedOpen"]>) => store.set("advancedOpen", value),
    setRemoveGrow: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["removeGrow"]>) => store.set("removeGrow", value),
    setAddGrow: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["addGrow"]>) => store.set("addGrow", value),
    setRemoveFeather: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["removeFeather"]>) => store.set("removeFeather", value),
    setAddFeather: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["addFeather"]>) => store.set("addFeather", value),
    setSeedText: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["seedText"]>) => store.set("seedText", value),
    setNCount: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["nCount"]>) => store.set("nCount", value),
    setRemoveModel: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["removeModel"]>) => store.set("removeModel", value),
    setAddModel: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["addModel"]>) => store.set("addModel", value),
    setInpaintProvider: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["inpaintProvider"]>) => store.set("inpaintProvider", value),
    setInpaintConfigLoaded: (value: React.SetStateAction<ReturnType<typeof store.getSnapshot>["inpaintConfigLoaded"]>) => store.set("inpaintConfigLoaded", value)  
}), [store]);
  return { ...context, actions };
}
export type InpaintSessionState = ReturnType<typeof useInpaintState>;
