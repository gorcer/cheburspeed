<script lang="ts">
  import { ChevronDown, ChevronRight, RefreshCw } from "@lucide/svelte";
  import { createQuery, useQueryClient } from "@tanstack/svelte-query";
  import { derived, writable } from "svelte/store";

  import {
    type AdminTaskDetail,
    type AdminTaskList,
    type ListAdminTasksParams,
    type TaskStatus,
    getAdminTask,
    listAdminTasks,
  } from "$lib/api/admin";
  import { t, locale } from "$lib/stores/locale.svelte";

  type TaskStatusFilter = TaskStatus | "all";

  const PAGE_SIZE = 20;

  const filtersStore = writable<{
    status: TaskStatusFilter;
    region: string;
    from: string;
    to: string;
    limit: number;
    offset: number;
  }>({
    status: "all",
    region: "",
    from: "",
    to: "",
    limit: PAGE_SIZE,
    offset: 0,
  });

  let filters = $state<{
    status: TaskStatusFilter;
    region: string;
    from: string;
    to: string;
    limit: number;
    offset: number;
  }>({
    status: "all",
    region: "",
    from: "",
    to: "",
    limit: PAGE_SIZE,
    offset: 0,
  });

  $effect(() => {
    filtersStore.set(filters);
  });

  const tasksOptions = derived(filtersStore, ($filters) => {
    const params: ListAdminTasksParams = {
      limit: $filters.limit,
      offset: $filters.offset,
    };
    if ($filters.status !== "all") params.status = $filters.status;
    if ($filters.region) params.region = $filters.region;
    if ($filters.from) params.from = new Date($filters.from).toISOString();
    if ($filters.to) params.to = new Date($filters.to).toISOString();
    return {
      queryKey: ["admin-tasks", $filters] as const,
      queryFn: () => listAdminTasks(params),
      refetchInterval: 30_000,
      staleTime: 10_000,
    };
  });
  const tasks = createQuery<
    AdminTaskList,
    Error,
    AdminTaskList,
    readonly ["admin-tasks", typeof filters]
  >(tasksOptions);

  const detailStore = writable<number | null>(null);
  let expanded = $state<number | null>(null);
  $effect(() => {
    detailStore.set(expanded);
  });
  const detailOptions = derived(detailStore, ($id) => ({
    queryKey: ["admin-task", $id] as const,
    queryFn: () =>
      $id === null
        ? Promise.resolve<AdminTaskDetail | null>(null)
        : getAdminTask($id),
    enabled: $id !== null,
  }));
  const detail = createQuery<
    AdminTaskDetail | null,
    Error,
    AdminTaskDetail | null,
    readonly ["admin-task", number | null]
  >(detailOptions);

  const queryClient = useQueryClient();

  function applyFilters(event: SubmitEvent) {
    event.preventDefault();
    filters = { ...filters, offset: 0 };
  }

  function resetFilters() {
    filters = {
      status: "all",
      region: "",
      from: "",
      to: "",
      limit: PAGE_SIZE,
      offset: 0,
    };
  }

  function refresh() {
    void $tasks.refetch();
  }

  function toggleRow(id: number) {
    expanded = expanded === id ? null : id;
  }

  function goPrev() {
    if (filters.offset === 0) return;
    filters = {
      ...filters,
      offset: Math.max(0, filters.offset - filters.limit),
    };
  }

  function goNext() {
    const total = $tasks.data?.total ?? 0;
    if (filters.offset + filters.limit >= total) return;
    filters = { ...filters, offset: filters.offset + filters.limit };
  }

  function statusClass(status: string): string {
    switch (status) {
      case "completed":
        return "bg-success/15 text-success border-success/30";
      case "running":
        return "bg-warning/15 text-warning border-warning/30";
      case "failed":
        return "bg-danger/15 text-danger border-danger/30";
      default:
        return "bg-surface-300/40 text-surface-content/70 border-surface-300";
    }
  }

  function formatDate(value: string | null): string {
    if (!value) return "—";
    try {
      return new Date(value).toLocaleString(
        locale.code === "ru" ? "ru-RU" : "en-US",
      );
    } catch {
      return value;
    }
  }

  function truncate(value: string, max = 56): string {
    if (value.length <= max) return value;
    return `${value.slice(0, max - 1)}…`;
  }

  const currentPageNumber = $derived(
    Math.floor(filters.offset / filters.limit) + 1,
  );
  const totalPages = $derived(
    Math.max(1, Math.ceil(($tasks.data?.total ?? 0) / filters.limit)),
  );
</script>

<svelte:head>
  <title>CheburSpeed · {t("admin.tabs.tasks")}</title>
</svelte:head>

<div class="space-y-5">
  <section
    aria-labelledby="task-filters-title"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <h2
      id="task-filters-title"
      class="text-sm font-semibold text-surface-content"
    >
      {t("admin.tasks.filtersTitle")}
    </h2>
    <form onsubmit={applyFilters} class="mt-3 grid gap-3 md:grid-cols-5">
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.tasks.filterStatus")}</span>
        <select
          bind:value={filters.status}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        >
          <option value="all">{t("admin.tasks.statusAll")}</option>
          <option value="pending">{t("results.status.pending")}</option>
          <option value="running">{t("results.status.running")}</option>
          <option value="completed">{t("results.status.completed")}</option>
          <option value="failed">{t("results.status.failed")}</option>
        </select>
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.tasks.filterRegion")}</span>
        <input
          type="text"
          bind:value={filters.region}
          maxlength={64}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.tasks.filterFrom")}</span>
        <input
          type="datetime-local"
          bind:value={filters.from}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.tasks.filterTo")}</span>
        <input
          type="datetime-local"
          bind:value={filters.to}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <div class="flex items-end gap-2">
        <button
          type="submit"
          class="rounded-pill bg-primary px-4 py-2 text-sm font-medium text-primary-content transition hover:brightness-110"
        >
          {t("admin.tasks.applyFilters")}
        </button>
        <button
          type="button"
          onclick={resetFilters}
          class="rounded-pill border border-surface-300 px-3 py-2 text-xs text-surface-content/70 transition hover:bg-surface-300/30"
        >
          {t("admin.tasks.resetFilters")}
        </button>
      </div>
    </form>
  </section>

  <section
    aria-labelledby="task-list-title"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <header class="flex flex-wrap items-center justify-between gap-2">
      <h2
        id="task-list-title"
        class="text-sm font-semibold text-surface-content"
      >
        {t("admin.tasks.listTitle")}
        {#if $tasks.data}
          <span class="ml-2 text-xs text-surface-content/60"
            >({$tasks.data.total})</span
          >
        {/if}
      </h2>
      <button
        type="button"
        onclick={refresh}
        class="inline-flex items-center gap-1.5 rounded-pill border border-surface-300 px-3 py-1.5 text-xs text-surface-content/70 transition hover:bg-surface-300/30"
        aria-label={t("common.retry")}
      >
        <RefreshCw size={12} aria-hidden="true" />
        <span>{t("common.retry")}</span>
      </button>
    </header>

    <div class="mt-3 overflow-x-auto">
      <table class="w-full min-w-[720px] text-left text-xs">
        <thead class="text-surface-content/60">
          <tr>
            <th class="w-8 py-2 pr-2"></th>
            <th class="py-2 pr-3">{t("admin.tasks.columns.id")}</th>
            <th class="py-2 pr-3">{t("admin.tasks.columns.link")}</th>
            <th class="py-2 pr-3">{t("admin.tasks.columns.status")}</th>
            <th class="py-2 pr-3">{t("admin.tasks.columns.results")}</th>
            <th class="py-2 pr-3">{t("admin.tasks.columns.created")}</th>
            <th class="py-2 pr-3">{t("admin.tasks.columns.completed")}</th>
          </tr>
        </thead>
        <tbody class="text-surface-content/85">
          {#if $tasks.isLoading}
            <tr>
              <td colspan="7" class="py-4 text-center text-surface-content/60">
                {t("common.loading")}
              </td>
            </tr>
          {:else if ($tasks.data?.items ?? []).length === 0}
            <tr>
              <td colspan="7" class="py-4 text-center text-surface-content/60">
                {t("admin.tasks.empty")}
              </td>
            </tr>
          {:else}
            {#each $tasks.data?.items ?? [] as task (task.id)}
              <tr class="border-t border-surface-300/60 align-top">
                <td class="py-2 pr-2">
                  <button
                    type="button"
                    onclick={() => toggleRow(task.id)}
                    class="text-surface-content/60 hover:text-primary"
                    aria-label={t("admin.tasks.toggle")}
                  >
                    {#if expanded === task.id}
                      <ChevronDown size={14} aria-hidden="true" />
                    {:else}
                      <ChevronRight size={14} aria-hidden="true" />
                    {/if}
                  </button>
                </td>
                <td class="py-2 pr-3 font-mono">{task.id}</td>
                <td class="py-2 pr-3">
                  <code class="text-[11px] text-surface-content/80"
                    >{truncate(task.link)}</code
                  >
                </td>
                <td class="py-2 pr-3">
                  <span
                    class={`inline-flex items-center rounded-pill border px-2 py-0.5 text-[11px] ${statusClass(task.status)}`}
                  >
                    {t(`results.status.${task.status}`)}
                  </span>
                </td>
                <td class="py-2 pr-3">
                  <span class="font-mono">{task.result_count}</span>
                  {#if task.error_count > 0}
                    <span class="ml-1 text-[11px] text-danger">
                      ({task.error_count}
                      {t("admin.tasks.errors")})
                    </span>
                  {/if}
                </td>
                <td class="py-2 pr-3 text-surface-content/60"
                  >{formatDate(task.created_at)}</td
                >
                <td class="py-2 pr-3 text-surface-content/60"
                  >{formatDate(task.completed_at)}</td
                >
              </tr>
              {#if expanded === task.id}
                <tr class="border-t border-surface-300/30 bg-surface-100/40">
                  <td colspan="7" class="px-2 py-3">
                    {#if $detail.isLoading}
                      <p class="text-xs text-surface-content/60">
                        {t("common.loading")}
                      </p>
                    {:else if $detail.data}
                      <div class="space-y-3 text-xs">
                        <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
                          <div>
                            <div class="text-surface-content/50">
                              {t("admin.tasks.detail.assignedTo")}
                            </div>
                            <div class="font-mono text-surface-content/85">
                              {$detail.data.assigned_to ?? "—"}
                            </div>
                          </div>
                          <div>
                            <div class="text-surface-content/50">
                              {t("admin.tasks.detail.startedAt")}
                            </div>
                            <div class="text-surface-content/85">
                              {formatDate($detail.data.started_at)}
                            </div>
                          </div>
                          <div>
                            <div class="text-surface-content/50">
                              {t("admin.tasks.detail.completedAt")}
                            </div>
                            <div class="text-surface-content/85">
                              {formatDate($detail.data.completed_at)}
                            </div>
                          </div>
                          <div>
                            <div class="text-surface-content/50">
                              {t("admin.tasks.detail.probeTokens")}
                            </div>
                            <div class="font-mono text-surface-content/85">
                              {($detail.data.probe_tokens ?? []).join(", ") ||
                                "—"}
                            </div>
                          </div>
                        </div>
                        <div>
                          <h3
                            class="mb-1 text-xs font-medium text-surface-content/80"
                          >
                            {t("admin.tasks.detail.title")}
                          </h3>
                          {#if $detail.data.results.length === 0}
                            <p class="text-surface-content/60">
                              {t("results.details.empty")}
                            </p>
                          {:else}
                            <table class="w-full text-left text-[11px]">
                              <thead class="text-surface-content/60">
                                <tr>
                                  <th class="py-1 pr-3"
                                    >{t("results.details.region")}</th
                                  >
                                  <th class="py-1 pr-3"
                                    >{t("results.details.probe")}</th
                                  >
                                  <th class="py-1 pr-3"
                                    >{t("results.details.speed")}</th
                                  >
                                  <th class="py-1 pr-3"
                                    >{t("results.details.error")}</th
                                  >
                                </tr>
                              </thead>
                              <tbody>
                                {#each $detail.data.results as result (result.id)}
                                  <tr class="border-t border-surface-300/40">
                                    <td class="py-1 pr-3">
                                      <span class="font-mono"
                                        >{result.region_code}</span
                                      >
                                      <span
                                        class="ml-1 text-surface-content/60"
                                      >
                                        {result.region_title}
                                      </span>
                                    </td>
                                    <td class="py-1 pr-3">
                                      <span class="font-mono"
                                        >{result.probe_id}</span
                                      >
                                      <span
                                        class="ml-1 text-surface-content/60"
                                      >
                                        {result.probe_alias || "—"}
                                      </span>
                                    </td>
                                    <td class="py-1 pr-3">
                                      {#if result.speed_mbps !== null}
                                        {result.speed_mbps.toFixed(2)}
                                      {:else}
                                        —
                                      {/if}
                                    </td>
                                    <td class="py-1 pr-3 text-danger">
                                      {result.error ?? ""}
                                    </td>
                                  </tr>
                                {/each}
                              </tbody>
                            </table>
                          {/if}
                        </div>
                      </div>
                    {/if}
                  </td>
                </tr>
              {/if}
            {/each}
          {/if}
        </tbody>
      </table>
    </div>

    {#if $tasks.data && $tasks.data.total > filters.limit}
      <div
        class="mt-3 flex items-center justify-between text-xs text-surface-content/60"
      >
        <span>
          {t("admin.probes.pageOf", {
            current: currentPageNumber,
            total: totalPages,
          })}
        </span>
        <div class="inline-flex items-center gap-2">
          <button
            type="button"
            onclick={goPrev}
            disabled={filters.offset === 0}
            class="rounded-pill border border-surface-300 px-3 py-1 transition hover:bg-surface-300/30 disabled:opacity-50"
          >
            {t("admin.probes.prev")}
          </button>
          <button
            type="button"
            onclick={goNext}
            disabled={filters.offset + filters.limit >=
              ($tasks.data?.total ?? 0)}
            class="rounded-pill border border-surface-300 px-3 py-1 transition hover:bg-surface-300/30 disabled:opacity-50"
          >
            {t("admin.probes.next")}
          </button>
        </div>
      </div>
    {/if}
  </section>

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</div>
