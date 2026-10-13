<script lang="ts">
  import '../app.css';

  import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';

  import AppShell from '$lib/components/AppShell.svelte';
  import { ws } from '$lib/api/ws.svelte';

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
      },
    },
  });

  let { children } = $props();

  $effect(() => {
    ws.connect();
    return () => ws.disconnect();
  });
</script>

<QueryClientProvider client={queryClient}>
  <AppShell>
    {@render children()}
  </AppShell>
</QueryClientProvider>