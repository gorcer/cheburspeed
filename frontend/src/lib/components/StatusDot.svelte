<script lang="ts">
  import { ws } from '$lib/api/ws.svelte';
  import { t } from '$lib/stores/locale.svelte';

  const colorClass = $derived.by(() => {
    if (ws.status === 'open') return 'bg-success shadow-[0_0_8px_var(--color-success)]';
    if (ws.status === 'connecting') return 'bg-warning animate-pulse';
    return 'bg-danger';
  });

  const label = $derived.by(() => {
    if (ws.status === 'open') return t('status.live');
    if (ws.status === 'connecting') return t('status.connecting');
    if (ws.retryIn !== null) return t('status.retryIn', { seconds: ws.retryIn });
    return t('status.offline');
  });
</script>

<span class="inline-flex items-center gap-2 text-xs text-surface-content/70">
  <span class="relative inline-flex h-2.5 w-2.5">
    <span
      class={`absolute inset-0 rounded-full ${colorClass}`}
      aria-hidden="true"
    ></span>
  </span>
  <span>{label}</span>
</span>