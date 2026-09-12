"use client";
import { useGenerationWorkspace } from './generation/useGenerationWorkspace';
import { GenerationView } from './generation/GenerationView';
export default function GenerationWorkspace() {
  const model = useGenerationWorkspace();
  return <GenerationView model={model} />;
}
