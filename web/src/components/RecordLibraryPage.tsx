"use client";
import { useRecordLibrary } from './records/useRecordLibrary';
import { RecordLibraryView } from './records/RecordLibraryView';
export default function RecordLibraryPage() { const model = useRecordLibrary(); return <RecordLibraryView model={model} />; }
