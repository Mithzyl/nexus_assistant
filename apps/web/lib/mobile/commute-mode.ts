import type { CommuteTransportMode } from "./api";

export const commuteModeLabels = {
  driving: "开车",
  walking: "步行",
  transit: "公交地铁",
  bicycling: "骑行",
  electrobike: "电动车",
} satisfies Record<CommuteTransportMode, string>;

export const commuteModes = Object.keys(commuteModeLabels) as CommuteTransportMode[];

/** Label a saved mode without treating an unsupported value as walking. */
export function commuteModeLabel(mode?: string | null): string {
  const known = commuteModes.find((value) => value === mode);
  return known ? commuteModeLabels[known] : "其他出行方式";
}
