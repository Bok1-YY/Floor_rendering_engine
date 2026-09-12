"use client";
import { RecordEditDialog } from './RecordEditDialog';
import { RecordReviewDialog } from './RecordReviewDialog';
import { RecordRevealDialog } from './RecordRevealDialog';
import { RecordImageDialogs } from './RecordImageDialogs';
import { RecordFilters } from './RecordFilters';
import { RecordSidebar } from './RecordSidebar';
import { RecordGroup } from './RecordGroup';

import type { RecordLibraryModel } from "./useRecordLibrary";

export function RecordLibraryView({ model }: { model: RecordLibraryModel }) {
  const {
    search,
    favoriteOnly,
    active,
    roomFilter,
    reviewFilter,
    loading,
    zoom,
    panoView,
    compare,
    colorMatch,
    inpaint,
    floorVisualize,
    reveal,
    edit,
    review,
    setSearch,
    setFavoriteOnly,
    setRoomFilter,
    setReviewFilter,
    setZoom,
    setPanoView,
    setCompare,
    setColorMatch,
    setInpaint,
    setFloorVisualize,
    setReveal,
    setEdit,
    setReview,
    open,
    reload,
    afterMutation,
    visibleFiles,
    totalFavorites,
    roomCounts,
    shownRecords,
    download,
    doDeleteResult,
    doFav,
    doReview,
    openReviewDialog,
    doReviewSubmit,
    compareBeforeUrl,
    doReuse,
    doDeleteRecord,
    doReveal,
    doEditSubmit
  } = model;
  return (
    <div className="flex h-full overflow-hidden">
      <RecordSidebar
        search={search}
        favoriteOnly={favoriteOnly}
        active={active}
        setSearch={setSearch}
        setFavoriteOnly={setFavoriteOnly}
        open={open}
        visibleFiles={visibleFiles}
        totalFavorites={totalFavorites}
        download={download}
      />
      {/* 右栏 */}
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
        {!active ? (
          <div className="flex flex-1 items-center justify-center">
            <div className="text-center text-muted-foreground">
              <svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" className="mx-auto mb-3 text-border-strong">
                <rect x="3" y="4" width="18" height="6" rx="1.6" />
                <rect x="3" y="14" width="18" height="6" rx="1.6" />
              </svg>
              <div className="text-[13.5px] font-semibold">
                从左侧选择一个材料记录查看
              </div>
            </div>
          </div>
        ) : (
          <>
            <RecordFilters
              active={active}
              roomFilter={roomFilter}
              reviewFilter={reviewFilter}
              setRoomFilter={setRoomFilter}
              setReviewFilter={setReviewFilter}
              reload={reload}
              roomCounts={roomCounts}
              download={download}
            />
            <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-[22px] py-[18px]">
              {loading && <div className="text-sm text-muted-foreground">加载中…</div>}
              {!loading && shownRecords.length === 0 && (
                <div className="flex flex-1 items-center justify-center rounded-[14px] border border-dashed border-border py-16 text-[13px] text-muted-foreground">
                  {favoriteOnly ? "当前筛选下没有收藏结果" : "当前筛选下没有结果"}
                </div>
              )}
              {!loading &&
                shownRecords.map((r, i) => (<RecordGroup
                  key={r.id || i}

                  setZoom={setZoom}
                  setPanoView={setPanoView}
                  setCompare={setCompare}
                  setColorMatch={setColorMatch}
                  setInpaint={setInpaint}
                  setFloorVisualize={setFloorVisualize}
                  setReveal={setReveal}
                  setEdit={setEdit}

                  download={download}
                  doDeleteResult={doDeleteResult}
                  doFav={doFav}
                  doReview={doReview}
                  openReviewDialog={openReviewDialog}
                  compareBeforeUrl={compareBeforeUrl}
                  doReuse={doReuse}
                  doDeleteRecord={doDeleteRecord}
                  r={r}
                  i={i}
                />))}
            </div>
          </>
        )}
      </section>

      <RecordImageDialogs
        active={active}
        zoom={zoom}
        panoView={panoView}
        compare={compare}
        colorMatch={colorMatch}
        inpaint={inpaint}
        floorVisualize={floorVisualize}
        setZoom={setZoom}
        setPanoView={setPanoView}
        setCompare={setCompare}
        setColorMatch={setColorMatch}
        setInpaint={setInpaint}
        setFloorVisualize={setFloorVisualize}

        afterMutation={afterMutation}
      />
      <RecordRevealDialog reveal={reveal} setReveal={setReveal} doReveal={doReveal} />
      <RecordReviewDialog review={review} setReview={setReview} doReviewSubmit={doReviewSubmit} />
      <RecordEditDialog edit={edit} setEdit={setEdit} doEditSubmit={doEditSubmit} />
    </div>
  );
}
