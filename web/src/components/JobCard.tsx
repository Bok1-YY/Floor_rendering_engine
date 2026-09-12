"use client";
import type { JobView } from '@/lib/types';
import { useJobCard } from './job-card/useJobCard';
import { JobCardView } from './job-card/JobCardView';
function JobCardSession({ initial, onRemove }: { initial: JobView; onRemove?: (id: string) => void }) {
  const model = useJobCard(initial, onRemove); return <JobCardView model={model} />;
}
export function JobCard(props: { initial: JobView; onRemove?: (id: string) => void }) {
  return <JobCardSession key={props.initial.job_id} {...props} />;
}
