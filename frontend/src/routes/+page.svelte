<script lang="ts">
  import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';

  const client = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
      },
    },
  });
</script>

<QueryClientProvider {client}>
  <main class="container">
    <h1>XSpeed</h1>
    <p>Сервис мониторинга скорости vless соединений.</p>
  </main>
</QueryClientProvider>

<style>
  .container {
    max-width: 960px;
    margin: 0 auto;
    padding: 2rem 1rem;
    font-family: system-ui, sans-serif;
  }
</style>