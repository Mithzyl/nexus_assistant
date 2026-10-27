/** Source-scoped Shortcut credential management. Credentials are never persisted. */

import {
  apiRequest,
  pa,
  type DataSource,
  type ShortcutToken,
  type ShortcutTokenCreated,
} from "./api";

export function listShortcutTokens(signal?: AbortSignal): Promise<ShortcutToken[]> {
  return apiRequest<ShortcutToken[]>(pa("/sync/ios/shortcut-tokens"), { signal });
}

export function createShortcutSource(input: {
  source_type: "calendar" | "reminders";
  device_id: string;
  display_name: string;
}): Promise<DataSource> {
  return apiRequest<DataSource>(pa("/data-sources"), {
    method: "POST",
    body: {
      ...input,
      sync_enabled: true,
      assistant_enabled: false,
    },
  });
}

export function createShortcutToken(
  sourceId: string,
  displayName: string,
): Promise<ShortcutTokenCreated> {
  return apiRequest<ShortcutTokenCreated>(pa("/sync/ios/shortcut-tokens"), {
    method: "POST",
    body: { source_id: sourceId, display_name: displayName },
  });
}

export function revokeShortcutToken(tokenId: string): Promise<ShortcutToken> {
  return apiRequest<ShortcutToken>(
    pa(`/sync/ios/shortcut-tokens/${encodeURIComponent(tokenId)}`),
    { method: "DELETE" },
  );
}
