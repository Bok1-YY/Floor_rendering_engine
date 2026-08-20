import type { WholeHomeOpening, WholeHomeWall } from "@/lib/types";

export type WholeHomeWallOpeningPart = WholeHomeOpening & { from: number; to: number };

const wallLength = (wall: WholeHomeWall) => Math.hypot(
  wall.end.x - wall.start.x,
  wall.end.z - wall.start.z,
);

/**
 * Project an opening authored on one CAD wall face onto all nearby parallel
 * faces of the same physical wall assembly.  A DWG usually contains both wall
 * outlines; rendering a hole on only one face produces a false solid wall.
 */
export function wallOpeningParts(
  wall: WholeHomeWall,
  openings: WholeHomeOpening[],
  walls: WholeHomeWall[],
): WholeHomeWallOpeningPart[] {
  const targetLength = wallLength(wall);
  if (targetLength <= 1e-6) return [];
  const targetDx = (wall.end.x - wall.start.x) / targetLength;
  const targetDz = (wall.end.z - wall.start.z) / targetLength;

  return openings
    .filter((opening) => opening.review_status !== "rejected")
    .flatMap((opening) => {
      const source = walls.find((candidate) => candidate.id === opening.wall_id);
      if (!source) return [];
      const sourceLength = wallLength(source);
      if (sourceLength <= 1e-6) return [];
      const sourceDx = (source.end.x - source.start.x) / sourceLength;
      const sourceDz = (source.end.z - source.start.z) / sourceLength;
      const parallel = Math.abs(sourceDx * targetDx + sourceDz * targetDz) >= Math.cos(6 * Math.PI / 180);
      if (!parallel) return [];

      const sourceStart = Math.max(0, Math.min(sourceLength, opening.offset_m));
      const sourceEnd = Math.max(sourceStart, Math.min(sourceLength, opening.offset_m + opening.width_m));
      const first = {
        x: source.start.x + sourceDx * sourceStart,
        z: source.start.z + sourceDz * sourceStart,
      };
      const second = {
        x: source.start.x + sourceDx * sourceEnd,
        z: source.start.z + sourceDz * sourceEnd,
      };
      const signedDistance = (point: { x: number; z: number }) =>
        (point.x - wall.start.x) * -targetDz + (point.z - wall.start.z) * targetDx;
      if (Math.max(Math.abs(signedDistance(first)), Math.abs(signedDistance(second))) > 0.40) return [];

      const project = (point: { x: number; z: number }) =>
        (point.x - wall.start.x) * targetDx + (point.z - wall.start.z) * targetDz;
      const projected = [project(first), project(second)].sort((a, b) => a - b);
      const from = Math.max(0, Math.min(targetLength, projected[0]));
      const to = Math.max(0, Math.min(targetLength, projected[1]));
      if (to - from <= 0.05) return [];
      return [{ ...opening, from, to }];
    })
    .sort((a, b) => a.from - b.from);
}
