"use client";
import { colorSessionKey, type ColorMatchDialogProps } from "./types";
export type { ColorMatchTarget } from "./types";
import { useColorMatchSession } from "./useColorMatchSession";
import { ColorMatchView } from "./ColorMatchView";
export function ColorMatchDialog(props: ColorMatchDialogProps) {
  if (!props.open) return null;
  return <ColorMatchSession key={colorSessionKey(props)} {...props} />;
}
function ColorMatchSession(props: ColorMatchDialogProps) { return <ColorMatchView {...useColorMatchSession(props)} />; }
