"use client";
import { useRecordQuery } from './useRecordQuery';
import { useRecordDialogs } from './useRecordDialogs';
import { useRecordActions } from './useRecordActions';
import { recordSelection, compareBeforeUrl } from './rules';
export function useRecordLibrary() {
  const query = useRecordQuery();
  const dialogs = useRecordDialogs(query);
  const actions = useRecordActions(query, dialogs);
  return {
    ...query, ...dialogs, ...actions, compareBeforeUrl,
    ...recordSelection(query.files, query.records, query.search, query.favoriteOnly, query.roomFilter, query.reviewFilter)
  };
}
export type RecordLibraryModel = ReturnType<typeof useRecordLibrary>;
