import { useQuery, type QueryClient } from "@tanstack/react-query";
import { providersApi } from "@/lib/api/providers";

export const piKeys = {
  all: ["pi"] as const,
  liveProviderIds: ["piLiveProviderIds"] as const,
  defaultProvider: ["pi", "defaultProvider"] as const,
  defaultModel: ["pi", "defaultModel"] as const,
};

export function invalidatePiProviderCaches(queryClient: QueryClient) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: piKeys.liveProviderIds }),
    queryClient.invalidateQueries({ queryKey: piKeys.defaultProvider }),
    queryClient.invalidateQueries({ queryKey: piKeys.defaultModel }),
  ]);
}

export function usePiLiveProviderIds(enabled: boolean) {
  return useQuery({
    queryKey: piKeys.liveProviderIds,
    queryFn: () => providersApi.getPiLiveProviderIds(),
    enabled,
  });
}

export function usePiDefaultProvider(enabled: boolean) {
  return useQuery({
    queryKey: piKeys.defaultProvider,
    queryFn: () => providersApi.getPiDefaultProvider(),
    enabled,
  });
}

export function usePiDefaultModel(enabled: boolean) {
  return useQuery({
    queryKey: piKeys.defaultModel,
    queryFn: () => providersApi.getPiDefaultModel(),
    enabled,
  });
}
