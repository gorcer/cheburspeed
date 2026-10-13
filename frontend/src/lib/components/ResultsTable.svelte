<script lang="ts">
  import { ChevronLeft, ChevronRight } from '@lucide/svelte';
  import { createQuery } from '@tanstack/svelte-query';
  import { derived, writable } from 'svelte/store';

  import { getTask, listTasks, type Task, type TaskList, type TaskSummary } from '$lib/api/tasks';
  import { t, locale } from '$lib/stores/locale.svelte';

  type Page = { limit: number; offset: number };

  const PAGE_SIZE = 10;

  const pageStore = writable<Page>({ limit: PAGE_SIZE, offset: 0 });

  let page = $state<Page>({ limit: PAGE_SIZE, offset: 0 });
  $effect(() => {
    pageStore.set(page);
  });

  const tasksOptions = derived(pageStore, ($page) => ({
    queryKey: ['tasks', $page] as const,
    queryFn: () => listTasks($page),
    refetchInterval: 30_000,
    staleTime: 10_000,
  }));
  const tasks = createQuery<TaskList, Error, TaskList, readonly ['tasks', Page]>(tasksOptions);

  const expandedStore = writable<number | null>(null);

  let expanded = $state<number | null>(null);
  $effect(() => {
    expandedStore.set(expanded);
  });

  const detailOptions = derived(expandedStore, ($expanded) => ({
    queryKey: ['task', $expanded] as const,
    queryFn: () => ($expanded === null ? Promise.resolve<Task | null>(null) : getTask($expanded)),
    enabled: $expanded !== null,
  }));
  const detail = createQuery<Task | null, Error, Task | null, readonly ['task', number | null]>(detailOptions);

  $effect(() => {
    if (expanded !== null) {
      void $detail.refetch();
    }
  });

  function toggleRow(id: number) {
    expanded = expanded === id ? null : id;
  }

  function statusClass(status: TaskSummary['status']): string {
    switch (status) {
      case 'completed':
        return 'bg-success/15 text-success border-success/30';
      case 'running':
        return 'bg-warning/15 text-warning border-warning/30';
      case 'failed':
        return 'bg-danger/15 text-danger border-danger/30';
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

  function truncate(value: string, max = 48): string {
    if (value.length <= max) return value;
    return `${value.slice(0, max - 1)}…`;
  }

  function goPrev() {
    if (page.offset === 0) return;
    page = { ...page, offset: Math.max(0, page.offset - page.limit) };
  }

  function goNext() {
    const total = $tasks.data?.total ?? 0;
    if (page.offset + page.limit >= total) return;
    page = { ...page, offset: page.offset + page.limit };
  }

  const totalPages = $derived.by(() => {
    const total = $tasks.data?.total ?? 0;
    return Math.max(1, Math.ceil(total / page.limit));
  });

  const currentPageNumber = $derived(Math.floor(page.offset / page.limit) + 1);
</script>

<section
  aria-labelledby="results-title"
  class="space-y-4 rounded-card border border-surface-300 bg-surface-200/60 p-5"
>
  <div class="flex items-center justify-between">
    <h2 id="results-title" class="text-base font-semibold text-surface-content">
      {t('results.title')}
    </h2>
    {#if $tasks.data}
      <span class="text-xs text-surface-content/60">
        {t('results.pagination.page', {
          page: currentPageNumber,
          total: totalPages,
        })}
      </span>
    {/if}
  </div>

  {#if $tasks.isLoading}
    <p class="text-sm text-surface-content/60">{t('common.loading')}</p>
  {:else if $tasks.isError}
    <p class="text-sm text-danger" role="alert">
      {t('common.error')}: {($tasks.error as Error | undefined)?.message ?? ''}
    </p>
  {:else if !$tasks.data || $tasks.data.items.length === 0}
    <p class="text-sm text-surface-content/60">{t('results.empty')}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="min-w-full text-left text-sm">
        <thead class="text-xs uppercase tracking-wide text-surface-content/50">
          <tr>
            <th class="px-3 py-2 font-medium">{t('results.columns.id')}</th>
            <th class="px-3 py-2 font-medium">{t('results.columns.link')}</th>
            <th class="px-3 py-2 font-medium">{t('results.columns.status')}</th>
            <th class="px-3 py-2 font-medium">{t('results.columns.created')}</th>
            <th class="px-3 py-2 font-medium">{t('results.columns.completed')}</th>
          </tr>
        </thead>
        <tbody>
          {#each $tasks.data.items as task (task.id)}
            <tr
              class="cursor-pointer border-t border-surface-300/60 transition hover:bg-surface-300/30"
              onclick={() => toggleRow(task.id)}
              onkeydown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  toggleRow(task.id);
                }
              }}
              tabindex="0"
            >
              <td class="px-3 py-2 font-mono text-surface-content/80">#{task.id}</td>
              <td class="max-w-[18rem] truncate px-3 py-2" title={task.link}>
                {truncate(task.link)}
              </td>
              <td class="px-3 py-2">
                <span
                  class={`inline-flex items-center rounded-pill px-2 py-0.5 text-xs border ${statusClass(task.status)}`}
                >
                  {t(`results.status.${task.status}`)}
                </span>
              </td>
              <td class="px-3 py-2 text-surface-content/70">{formatDate(task.created_at)}</td>
              <td class="px-3 py-2 text-surface-content/70">{formatDate(task.completed_at)}</td>
            </tr>
            {#if expanded === task.id}
              <tr class="border-t border-surface-300/40 bg-surface-100/40">
                <td colspan="5" class="px-3 py-3">
                  {#if $detail.isLoading}
                    <p class="text-xs text-surface-content/60">{t('common.loading')}</p>
                  {:else if $detail.isError || !$detail.data}
                    <p class="text-xs text-danger" role="alert">
                      {t('common.error')}: {($detail.error as Error | undefined)?.message ?? ''}
                    </p>
                  {:else}
                    {@const detailData = $detail.data as Task}
                    {#if detailData.results.length === 0}
                      <p class="text-xs text-surface-content/60">
                        {t('results.details.empty')}
                      </p>
                    {:else}
                      <div class="overflow-x-auto">
                        <table class="min-w-full text-left text-xs">
                          <thead class="text-surface-content/50">
                            <tr>
                              <th class="px-2 py-1 font-medium">
                                {t('results.columns.probes')}
                              </th>
                              <th class="px-2 py-1 font-medium">
                                {t('results.columns.id')}
                              </th>
                              <th class="px-2 py-1 font-medium">
                                {t('results.details.speed')}
                              </th>
                              <th class="px-2 py-1 font-medium">
                                {t('results.details.error')}
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {#each detailData.results as result (result.id)}
                              <tr class="border-t border-surface-300/40">
                                <td class="px-2 py-1 font-mono">#{result.probe_id}</td>
                                <td class="px-2 py-1 font-mono text-surface-content/60">
                                  #{result.id}
                                </td>
                                <td class="px-2 py-1 text-surface-content/80">
                                  {#if result.speed_mbps !== null}
                                    {t('results.details.speedValue', {
                                      value: result.speed_mbps.toFixed(2),
                                    })}
                                  {:else}
                                    —
                                  {/if}
                                </td>
                                <td class="px-2 py-1 text-danger/80">{result.error ?? '—'}</td>
                              </tr>
                            {/each}
                          </tbody>
                        </table>
                      </div>
                    {/if}
                  {/if}
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-between pt-2">
      <button
        type="button"
        onclick={goPrev}
        disabled={page.offset === 0}
        class="inline-flex items-center gap-1 rounded-pill border border-surface-300 px-3 py-1.5 text-xs text-surface-content/70 transition hover:bg-surface-300/30 disabled:cursor-not-allowed disabled:opacity-40"
      >
        <ChevronLeft size={14} aria-hidden="true" />
        {t('results.pagination.prev')}
      </button>
      <button
        type="button"
        onclick={goNext}
        disabled={page.offset + page.limit >= ($tasks.data?.total ?? 0)}
        class="inline-flex items-center gap-1 rounded-pill border border-surface-300 px-3 py-1.5 text-xs text-surface-content/70 transition hover:bg-surface-300/30 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {t('results.pagination.next')}
        <ChevronRight size={14} aria-hidden="true" />
      </button>
    </div>
  {/if}

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</section>