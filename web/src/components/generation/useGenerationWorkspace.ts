"use client";
import { useEffect, useRef, useState } from 'react';
import type { FloorUploaderHandle } from '@/components/FloorUploader';
import { useGenerationState } from './useGenerationState';
import { useFloorAnalysis } from './useFloorAnalysis';
import { useGenerationJobs } from './useGenerationJobs';
import { useGenerationPreview } from './useGenerationPreview';
import { useGenerationRecipes } from './useGenerationRecipes';
import { useGenerationSubmit } from './useGenerationSubmit';
export function useGenerationWorkspace() {
  const form = useGenerationState();
  const floors = useFloorAnalysis(form);
  const tasks = useGenerationJobs();
  const previewState = useGenerationPreview(form);
  const recipeState = useGenerationRecipes(form);
  const submission = useGenerationSubmit(form, tasks.addJobs);
  const [openStep, setOpenStep] = useState<0 | 1 | 2 | 3>(2);
  const [zoom, setZoom] = useState<string | null>(null);
  const floorUploaderRef = useRef<FloorUploaderHandle>(null);
  const initialized = useRef(false);
  const { options, floor, params, modelTargets } = form;
  const { jobs, jobsLoaded } = tasks;
  useEffect(() => { if (!initialized.current && jobsLoaded && options) { initialized.current = true; setOpenStep(!floor && jobs.length === 0 ? 1 : 2); } }, [jobsLoaded, options, floor, jobs.length]);
  const total = jobs.length;
  const activeCount = jobs.filter(j => j.status === 'queued' || j.status === 'running' || j.pro_polishing || j.operation_status === 'running').length;
  const doneCount = total - activeCount;
  return {
    ...form, ...floors, ...tasks, ...previewState, ...recipeState, ...submission,
    openStep, setOpenStep, zoom, setZoom, floorUploaderRef, total, activeCount, doneCount,
    pct: total ? Math.round(doneCount / total * 100) : 0,
    isFreeMode: params.workflow_mode.includes('自由创作'),
    batchRoomOptions: options ? params.cn_mode ? options.cn_room_types : options.room_types : [],
    outputLabel: modelTargets.map(key => ({ b2: 'B2', pro: 'Pro', sd35: 'SD 3.5' })[key]).join(' + '),
  };
}
export type GenerationWorkspaceModel = ReturnType<typeof useGenerationWorkspace>;
