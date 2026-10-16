<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { onMount } from 'svelte';
  import { derived } from 'svelte/store';

  import { getTask, type Task, type TaskResult } from '$lib/api/tasks';
  import { ws } from '$lib/api/ws.svelte';
  import { currentTaskId } from '$lib/stores/currentTask.svelte';
  import { locale, t } from '$lib/stores/locale.svelte';

  const currentTaskOptions = derived(currentTaskId, ($id) => ({
    queryKey: ['currentTask', $id] as const,
    queryFn: () =>
      $id === null ? Promise.resolve<Task | null>(null) : getTask($id),
    enabled: $id !== null,
    refetchInterval: 15_000,
  }));
  const currentTaskQuery = createQuery<
    Task | null,
    Error,
    Task | null,
    readonly ['currentTask', number | null]
  >(currentTaskOptions);

  const results = $derived($currentTaskQuery.data?.results ?? []);

  function statusLabel(status: Task['status'] | undefined): string {
    if (!status) return '';
    return t(`results.status.${status}`);
  }

  function statusClass(status: Task['status'] | undefined): string {
    switch (status) {
      case 'completed':
        return 'bg-success/15 text-success border-success/30';
      case 'running':
        return 'bg-warning/15 text-warning border-warning/30';
      case 'failed':
        return 'bg-danger/15 text-danger border-danger/30';
      case 'pending':
        return 'bg-surface-300/40 text-surface-content/70 border-surface-300';
      default:
        return 'bg-surface-300/40 text-surface-content/70 border-surface-300';
    }
  }

  function formatDate(value: string | null): string {
    if (!value) return '—';
    try {
      return new Date(value).toLocaleString(locale.code === 'ru' ? 'ru-RU' : 'en-US');
    } catch {
      return value;
    }
  }

  function speedCell(result: TaskResult): string {
    if (result.error) return '—';
    if (result.speed_mbps === null || result.speed_mbps === undefined) return '—';
    return t('results.details.speedValue', { value: result.speed_mbps.toFixed(2) });
  }

  function speedToneClass(result: TaskResult): string {
    if (result.error) return 'text-danger/80';
    if (result.speed_mbps === null) return 'text-surface-content/60';
    if (result.speed_mbps < 5) return 'text-danger';
    if (result.speed_mbps < 20) return 'text-warning';
    return 'text-success';
  }

  const address = $derived($currentTaskQuery.data?.address ?? null);

  onMount(() => {
    const off = ws.on((event) => {
      if (event.type === 'result.added' || event.type === 'task.updated') {
        void $currentTaskQuery.refetch();
      }
    });
    return off;
  });
</script>

<section
  aria-labelledby="current-task-title"
  class="space-y-4 rounded-card border border-surface-300 bg-surface-200/60 p-5"
>
  <div class="flex flex-wrap items-center justify-between gap-2">
    <h2
      id="current-task-title"
      class="text-base font-semibold text-surface-content"
    >
      {t('results.title')}
    </h2>
    {#if $currentTaskQuery.data}
      <span
        class={`inline-flex items-center rounded-pill border px-2 py-0.5 text-xs ${statusClass($currentTaskQuery.data.status)}`}
      >
        {statusLabel($currentTaskQuery.data.status)}
      </span>
    {/if}
  </div>

  {#if $currentTaskQuery.data}
    <p class="text-sm text-surface-content/70">
      <span class="text-surface-content/50">{t('results.details.address')}:</span>
      <span class="ml-1 font-mono text-surface-content">
        {address ?? '—'}
      </span>
    </p>
  {/if}

  {#if $currentTaskId === null}
    <p class="text-sm text-surface-content/60">{t('results.empty')}</p>
  {:else if $currentTaskQuery.isLoading}
    <p class="text-sm text-surface-content/60">{t('common.loading')}</p>
  {:else if $currentTaskQuery.isError}
    <p class="text-sm text-danger" role="alert">
      {t('common.error')}: {($currentTaskQuery.error as Error | undefined)?.message ?? ''}
    </p>
  {:else if !$currentTaskQuery.data}
    <p class="text-sm text-surface-content/60">{t('results.empty')}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="min-w-full text-left text-sm">
        <thead class="text-xs uppercase tracking-wide text-surface-content/50">
          <tr>
            <th class="px-3 py-2 font-medium">{t('results.details.probe')}</th>
            <th class="px-3 py-2 font-medium">{t('results.details.region')}</th>
            <th class="px-3 py-2 font-medium">{t('results.details.speed')}</th>
            <th class="px-3 py-2 font-medium">{t('results.details.error')}</th>
            <th class="px-3 py-2 font-medium">{t('results.details.created')}</th>
          </tr>
        </thead>
        <tbody>
          {#each results as result (result.id)}
            <tr class="border-t border-surface-300/60">
              <td class="px-3 py-2 font-medium text-surface-content">
                {result.probe_alias || `probe-${result.probe_id}`}
              </td>
              <td class="px-3 py-2 text-surface-content/70">
                {result.region_title}
              </td>
              <td class={`px-3 py-2 font-mono ${speedToneClass(result)}`}>
                {speedCell(result)}
              </td>
              <td class="px-3 py-2 text-danger/80">{result.error ?? '—'}</td>
              <td class="px-3 py-2 text-surface-content/70">
                {formatDate(result.created_at)}
              </td>
            </tr>
          {/each}
          {#if results.length === 0}
            <tr>
              <td colspan="5" class="px-3 py-4 text-center text-surface-content/60">
                {t('results.details.empty')}
              </td>
            </tr>
          {/if}
        </tbody>
      </table>
    </div>
  {/if}

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</section>