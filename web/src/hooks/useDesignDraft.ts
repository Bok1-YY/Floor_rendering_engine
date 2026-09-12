"use client";
import { useEffect, useRef, useState } from "react";
import type { WholeHomeDesignProject, DesignReferenceUpload, DesignPlanRoom } from "@/lib/types";
const csv = (items?: string[]) => (items || []).join("\n");

export function useDesignDraft(project: WholeHomeDesignProject | null) {
  const owner = useRef<string | null>(null);
  const dirty = useRef({ summary: false, brief: false });
  const edits = useRef({ summary: 0, brief: 0 });
  const revisions = useRef({ summary: 0, brief: 0 });
  const [references, setReferences] = useState<DesignReferenceUpload[]>([]);
  const [requirements, setRequirements] = useState("");
  const [roomsState, setRoomsState] = useState<DesignPlanRoom[]>([]);
  const [declaredLayout, setDeclaredLayout] = useState({ bedrooms: 0, halls: 0, bathrooms: 0, source_text: "", confidence: 0 });
  const [declaredArea, setDeclaredArea] = useState(0);
  const [overallDimensions, setOverallDimensions] = useState({ width: 0, depth: 0, evidence: [] as string[], confidence: 0 });
  const [listFields, setListFields] = useState({
    entrances: "", openings_summary: "", wet_zones: "", balconies: "",
    dimension_evidence: "", must_preserve: "", uncertainties: "",
  });
  useEffect(() => {
    if (!project) return;
    const timer = window.setTimeout(() => {
      if (owner.current !== project.project_id) {
        owner.current = project.project_id;
        dirty.current = { summary: false, brief: false };
        setReferences((project.brief?.reference_paths || []).map((path, i) => ({
          path, name: path.split(/[\\/]/).pop() || path, url: `/uploads/${path.split(/[\\/]/).pop()}`,
          thumb: "", sha256: project.brief?.reference_hashes?.[i] || path,
        })));
      }
      if (!dirty.current.summary) {
      setRoomsState(project.plan_summary?.rooms || []);
      setDeclaredLayout(project.plan_summary?.declared_layout || { bedrooms: 0, halls: 0, bathrooms: 0, source_text: "", confidence: 0 });
      setDeclaredArea(project.plan_summary?.declared_area_m2 || 0);
      setOverallDimensions(project.plan_summary?.overall_dimensions_mm || { width: 0, depth: 0, evidence: [], confidence: 0 });
      setListFields({
        entrances: csv(project.plan_summary?.entrances || []),
        openings_summary: csv(project.plan_summary?.openings_summary || []),
        wet_zones: csv(project.plan_summary?.wet_zones || []),
        balconies: csv(project.plan_summary?.balconies || []),
        dimension_evidence: csv(project.plan_summary?.dimension_evidence || []),
        must_preserve: csv(project.plan_summary?.must_preserve || []),
        uncertainties: csv(project.plan_summary?.uncertainties || []),
      });
      }
      if (!dirty.current.brief) setRequirements(project.brief?.requirements_text || "");
    }, 0);
    return () => window.clearTimeout(timer);
  }, [project]);

  return {
    references,
    setReferences: (value: Parameters<typeof setReferences>[0]) => { if (!dirty.current.brief) revisions.current.brief = project?.revision || 0; dirty.current.brief = true; ++edits.current.brief; setReferences(value); },
    requirements,
    setRequirements: (value: Parameters<typeof setRequirements>[0]) => { if (!dirty.current.brief) revisions.current.brief = project?.revision || 0; dirty.current.brief = true; ++edits.current.brief; setRequirements(value); },
    roomsState,
    setRoomsState: (value: Parameters<typeof setRoomsState>[0]) => { if (!dirty.current.summary) revisions.current.summary = project?.revision || 0; dirty.current.summary = true; ++edits.current.summary; setRoomsState(value); },
    declaredLayout,
    setDeclaredLayout: (value: Parameters<typeof setDeclaredLayout>[0]) => { if (!dirty.current.summary) revisions.current.summary = project?.revision || 0; dirty.current.summary = true; ++edits.current.summary; setDeclaredLayout(value); },
    declaredArea,
    setDeclaredArea: (value: Parameters<typeof setDeclaredArea>[0]) => { if (!dirty.current.summary) revisions.current.summary = project?.revision || 0; dirty.current.summary = true; ++edits.current.summary; setDeclaredArea(value); },
    overallDimensions,
    setOverallDimensions: (value: Parameters<typeof setOverallDimensions>[0]) => { if (!dirty.current.summary) revisions.current.summary = project?.revision || 0; dirty.current.summary = true; ++edits.current.summary; setOverallDimensions(value); },
    listFields,
    setListFields: (value: Parameters<typeof setListFields>[0]) => { if (!dirty.current.summary) revisions.current.summary = project?.revision || 0; dirty.current.summary = true; ++edits.current.summary; setListFields(value); },
    baseRevision: (kind: "summary" | "brief") => dirty.current[kind] ? revisions.current[kind] : project?.revision || 0,
    editToken: (kind: "summary" | "brief") => edits.current[kind],
    rebase: (kind: "summary" | "brief", id: string, revision: number) => { if (owner.current === id) revisions.current[kind] = revision; },
    markSaved: (kind: "summary" | "brief", id: string, token: number, revision: number) => {
      if (owner.current !== id) return;
      revisions.current[kind] = revision;
      if (edits.current[kind] === token) dirty.current[kind] = false;
    },
  };
}
