import { useQuery, type QueryClient } from "@tanstack/react-query";
import { providersApi } from "@/lib/api/providers";

/**
 * Centralized query keys for all Reasonix-related queries.
 * Import this from any file that needs to invalidate Reasonix caches.
 */
export const reasonixKeys = {
  all: ["reasonix"] as const,
  liveProviderIds: ["reasonixLiveProviderIds"] as const,
  defaultModel: ["reasonix", "defaultModel"] as const,
};

/**
 * Invalidate Reasonix caches that may change when a provider is
 * added/updated/deleted/switched/imported/takeover-restored.
 */
export function invalidateReasonixProviderCaches(queryClient: QueryClient) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: reasonixKeys.liveProviderIds }),
    queryClient.invalidateQueries({ queryKey: reasonixKeys.defaultModel }),
  ]);
}

// ============================================================
// Query hooks
// ============================================================

export function useReasonixLiveProviderIds(enabled: boolean) {
  return useQuery({
    queryKey: reasonixKeys.liveProviderIds,
    queryFn: () => providersApi.getReasonixLiveProviderIds(),
    enabled,
  });
}

export function useReasonixDefaultModel(enabled: boolean) {
  return useQuery({
    queryKey: reasonixKeys.defaultModel,
    queryFn: () => providersApi.getReasonixDefaultModel(),
    enabled,
  });
}
