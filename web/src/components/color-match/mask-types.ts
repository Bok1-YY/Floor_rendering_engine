import type { ColorMatchRect } from "@/lib/types";
export type ColorMaskProps = {
  imageUrl: string;
  imageRel: string;
  compact?: boolean;
  onMaskChange: (maskB64: string, rect: ColorMatchRect) => void;
  onBusyChange?: (busy: boolean) => void;
};
