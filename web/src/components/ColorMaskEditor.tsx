"use client";
import type {ColorMaskProps} from "./color-match/mask-types";
import {useColorMask} from "./color-match/useColorMask";
import {ColorMaskView} from "./color-match/ColorMaskView";
export function ColorMaskEditor(props:ColorMaskProps){return <Session key={JSON.stringify([props.imageRel,props.imageUrl])} {...props}/>;}
function Session(props:ColorMaskProps){return <ColorMaskView {...useColorMask(props)}/>;}
