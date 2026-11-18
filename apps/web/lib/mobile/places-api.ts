/** Body-only, draft-free place lookups shared by commute and schedule editors. */

import { apiRequest, pa, type MapPickResponse, type PlaceCandidate, type PlaceSearchResponse } from "./api";

export type PlaceLookupCandidate = Omit<PlaceCandidate, "selection_token">;

/** Search candidates do not contain tokens for applying a schedule draft selection. */
export interface PlaceLookupResponse extends Omit<PlaceSearchResponse, "candidates"> {
  candidates: PlaceLookupCandidate[];
}

export type PlacePointLocation = Omit<NonNullable<MapPickResponse["location"]>, "selection_token">;

/** A chosen coordinate resolves to a nearby address, not necessarily a named POI. */
export interface PlacePointResponse extends Omit<MapPickResponse, "location"> {
  location: PlacePointLocation | null;
}

/** Search without a business draft; all conditions are sent in the JSON body. */
export function searchPlaceCandidates(
  input: { query: string; city?: string | null; search_type?: "place" | "address"; limit?: number },
  signal?: AbortSignal,
): Promise<PlaceLookupResponse> {
  return apiRequest<PlaceLookupResponse>(pa("/places/search"), {
    method: "POST", body: input, signal,
  });
}

/** Reverse lookup one explicit GCJ-02 map point without creating a draft or saving it. */
export function lookupMapPoint(
  point: { latitude: number; longitude: number; coordinate_system?: "gcj02" },
  signal?: AbortSignal,
): Promise<PlacePointResponse> {
  return apiRequest<PlacePointResponse>(pa("/places/map-pick"), {
    method: "POST", body: point, signal,
  });
}
