<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';

  import { getTask, type Task, type TaskResult } from '$lib/api/tasks';
  import { ws } from '$lib/api/ws.svelte';
  import { currentTaskId } from '$lib/stores/currentTask.svelte';
  import { historyChecks, type HistoryEntry } from '$lib/stores/historyChecks.svelte';
  import { locale, t } from '$lib/stores/locale.svelte';

  function formatDate(value: string | null): string {
    if (!value) return '—';
    try {
      return new Date(value).toLocaleString(locale.code === 'ru' ? 'ru-RU' : 'en-US');
    } catch {
      return value;
    }
  }

  function speedCell(entry: HistoryEntry): string {
    if (entry.speedMbps === null || entry.speedMbps === undefined) return '—';
    return t('results.details.speedValue', { value: entry.speedMbps.toFixed(2) });
  }

  function speedToneClass(entry: HistoryEntry): string {
    if (entry.speedMbps === null) return 'text-surface-content/60';
    if (entry.speedMbps < 5) return 'text-danger';
    if (entry.speedMbps < 20) return 'text-warning';
    return 'text-success';
  }

  function probeLabel(entry: HistoryEntry): string {
    return entry.probeAlias || `probe-${entry.probeId}`;
  }

  function entryFromResult(
    result: TaskResult,
    taskId: number,
    destAddress: string,
  ): HistoryEntry {
    return {
      taskId,
      probeId: result.probe_id,
      probeAlias: result.probe_alias,
      speedMbps: result.speed_mbps,
      destAddress,
      createdAt: result.created_at,
    };
  }

  async function syncFromTask(taskId: number): Promise<void> {
    let task: Task;
    try {
      task = await getTask(taskId);
    } catch {
      return;
    }
    const destAddress = task.destination ?? '—';
    for (const result of task.results) {
      historyChecks.add(entryFromResult(result, taskId, destAddress));
    }
  }

  onMount(() => {
    const offResult = ws.onEvent('result.added', async (payload) => {
      const taskId = get(currentTaskId);
      if (taskId === null) return;
      if (payload.task_id !== taskId) return;
      await syncFromTask(taskId);
    });

    const offTask = ws.onEvent('task.updated', async (payload) => {
      if (payload.status !== 'completed') return;
      const currentId = get(currentTaskId);
      if (currentId === null || payload.id !== currentId) return;
      await syncFromTask(currentId);
    });

    return () => {
      offResult();
      offTask();
    };
  });
</script>

<section
  aria-labelledby="history-title"
  class="space-y-4 rounded-card border border-surface-300 bg-surface-200/60 p-5"
>
  <div class="flex flex-wrap items-center justify-between gap-2">
    <h2 id="history-title" class="text-base font-semibold text-surface-content">
      {t('history.title')}
    </h2>
  </div>

  {#if historyChecks.entries.length === 0}
    <p class="text-sm text-surface-content/60">{t('history.empty')}</p>
  {:else}
    <div class="overflow-x-auto">
      <table class="min-w-full text-left text-sm">
        <thead class="text-xs uppercase tracking-wide text-surface-content/50">
          <tr>
            <th class="px-3 py-2 font-medium">{t('history.columns.probeId')}</th>
            <th class="px-3 py-2 font-medium">{t('history.columns.probeName')}</th>
            <th class="px-3 py-2 font-medium">{t('history.columns.speed')}</th>
            <th class="px-3 py-2 font-medium">{t('history.columns.dest')}</th>
            <th class="px-3 py-2 font-medium">{t('history.columns.date')}</th>
          </tr>
        </thead>
        <tbody>
          {#each historyChecks.entries as entry (`${entry.taskId}:${entry.probeId}`)}
            <tr class="border-t border-surface-300/60">
              <td class="px-3 py-2 font-mono text-surface-content/80">{entry.probeId}</td>
              <td class="px-3 py-2 font-medium text-surface-content">
                {probeLabel(entry)}
              </td>
              <td class={`px-3 py-2 font-mono ${speedToneClass(entry)}`}>
                {speedCell(entry)}
              </td>
              <td class="px-3 py-2 text-surface-content/70">{entry.destAddress}</td>
              <td class="px-3 py-2 text-surface-content/70">{formatDate(entry.createdAt)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>
