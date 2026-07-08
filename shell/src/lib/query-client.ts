import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000, // 30 s
      gcTime: 5 * 60_000, // 5 min
      retry: (failureCount, error) => {
        const msg = (error as Error)?.message ?? '';
        // Don't retry on 4xx, but do retry on network/5xx
        if (msg.includes('HTTP 4')) return false;
        return failureCount < 2;
      },
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
});
