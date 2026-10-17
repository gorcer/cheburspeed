<script lang="ts">
  import { Globe, RefreshCw } from "@lucide/svelte";
  import { createQuery, useQueryClient } from "@tanstack/svelte-query";
  import { derived, writable } from "svelte/store";

  import {
    type AdminMetric,
    type AdminMetricList,
    createAdminMetric,
    listAdminMetrics,
  } from "$lib/api/metrics";
  import { listRegions, type RegionList } from "$lib/api/probes";
  import { t, locale } from "$lib/stores/locale.svelte";

  const PAGE_SIZE = 20;

  const pageStore = writable<{ limit: number; offset: number }>({
    limit: PAGE_SIZE,
    offset: 0,
  });
  let page = $state<{ limit: number; offset: number }>({
    limit: PAGE_SIZE,
    offset: 0,
  });
  $effect(() => {
    pageStore.set(page);
  });

  const metricsOptions = derived(pageStore, ($page) => ({
    queryKey: ["admin-metrics", $page] as const,
    queryFn: () => listAdminMetrics($page),
    refetchInterval: 30_000,
    staleTime: 10_000,
  }));
  const metrics = createQuery<
    AdminMetricList,
    Error,
    AdminMetricList,
    readonly ["admin-metrics", { limit: number; offset: number }]
  >(metricsOptions);

  const regionsQuery = createQuery<
    RegionList,
    Error,
    RegionList,
    readonly ["regions"]
  >({
    queryKey: ["regions"] as const,
    queryFn: listRegions,
    staleTime: 5 * 60_000,
  });

  const queryClient = useQueryClient();

  let formClientIp = $state("");
  let formClientIsp = $state("");
  let formClientLat = $state("");
  let formClientLng = $state("");
  let formServerId = $state<number | null>(null);
  let formIp = $state("");
  let formMetricAlias = $state("");
  let formValue = $state("");
  let formError = $state<string | null>(null);
  let formSubmitting = $state(false);

  function resetForm() {
    formClientIp = "";
    formClientIsp = "";
    formClientLat = "";
    formClientLng = "";
    formServerId = null;
    formIp = "";
    formMetricAlias = "";
    formValue = "";
    formError = null;
  }

  function parseOptionalNumber(value: string): number | null {
    if (!value.trim()) return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      throw new Error("NaN");
    }
    return parsed;
  }

  async function handleCreate(event: SubmitEvent) {
    event.preventDefault();
    if (formServerId === null) {
      formError = t("admin.metrics.serverRequired");
      return;
    }
    formError = null;
    formSubmitting = true;
    try {
      const payload = {
        client_ip: formClientIp.trim(),
        client_isp: formClientIsp.trim() || null,
        client_lat: parseOptionalNumber(formClientLat),
        client_lng: parseOptionalNumber(formClientLng),
        server_id: formServerId,
        ip: formIp.trim() || null,
        metric_alias: formMetricAlias.trim(),
        value: parseOptionalNumber(formValue),
      };
      await createAdminMetric(payload);
      resetForm();
      void queryClient.invalidateQueries({ queryKey: ["admin-metrics"] });
    } catch (err) {
      formError = err instanceof Error ? err.message : t("common.error");
    } finally {
      formSubmitting = false;
    }
  }

  function refresh() {
    void $metrics.refetch();
  }

  function goPrev() {
    if (page.offset === 0) return;
    page = { ...page, offset: Math.max(0, page.offset - page.limit) };
  }

  function goNext() {
    const total = $metrics.data?.total ?? 0;
    if (page.offset + page.limit >= total) return;
    page = { ...page, offset: page.offset + page.limit };
  }

  const currentPageNumber = $derived(Math.floor(page.offset / page.limit) + 1);
  const totalPages = $derived(
    Math.max(1, Math.ceil(($metrics.data?.total ?? 0) / page.limit)),
  );

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

  function formatValue(value: number | null): string {
    if (value === null || value === undefined)
      return t("admin.metrics.emptyValue");
    return Number(value).toFixed(2);
  }

  function mapsUrl(metric: AdminMetric): string | null {
    if (metric.client_lat === null || metric.client_lng === null) return null;
    return `https://www.google.com/maps?q=${metric.client_lat},${metric.client_lng}`;
  }
</script>

<svelte:head>
  <title>XSpeed · {t("admin.tabs.metrics")}</title>
</svelte:head>

<div class="space-y-5">
  <section
    aria-labelledby="metric-form-title"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <h2
      id="metric-form-title"
      class="text-sm font-semibold text-surface-content"
    >
      {t("admin.metrics.createTitle")}
    </h2>
    <form onsubmit={handleCreate} class="mt-3 grid gap-3 md:grid-cols-3">
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.metrics.clientIp")}</span>
        <input
          type="text"
          bind:value={formClientIp}
          required
          maxlength={64}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.metrics.clientIsp")}</span>
        <input
          type="text"
          bind:value={formClientIsp}
          maxlength={255}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.metrics.serverId")}</span>
        <select
          bind:value={formServerId}
          required
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        >
          <option value={null}>—</option>
          {#each $regionsQuery.data?.items ?? [] as region (region.id)}
            <option value={region.id}>{region.title} ({region.code})</option>
          {/each}
        </select>
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.metrics.clientLat")}</span>
        <input
          type="number"
          step="any"
          bind:value={formClientLat}
          min={-90}
          max={90}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.metrics.clientLng")}</span>
        <input
          type="number"
          step="any"
          bind:value={formClientLng}
          min={-180}
          max={180}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.metrics.ip")}</span>
        <input
          type="text"
          bind:value={formIp}
          maxlength={64}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.metrics.metricAlias")}</span>
        <input
          type="text"
          bind:value={formMetricAlias}
          required
          maxlength={128}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.metrics.value")}</span>
        <input
          type="number"
          step="any"
          bind:value={formValue}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      {#if formError}
        <p class="text-xs text-danger md:col-span-3" role="alert">
          {formError}
        </p>
      {/if}
      <div class="md:col-span-3">
        <button
          type="submit"
          disabled={formSubmitting}
          class="inline-flex items-center rounded-pill bg-primary px-4 py-2 text-sm font-medium text-primary-content transition hover:brightness-110 disabled:opacity-60"
        >
          {formSubmitting ? t("common.loading") : t("admin.metrics.submit")}
        </button>
      </div>
    </form>
  </section>

  <section
    aria-labelledby="metric-list-title"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <header class="flex flex-wrap items-center justify-between gap-2">
      <h2
        id="metric-list-title"
        class="text-sm font-semibold text-surface-content"
      >
        {t("admin.metrics.listTitle")}
        {#if $metrics.data}
          <span class="ml-2 text-xs text-surface-content/60">
            ({$metrics.data.total})
          </span>
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
      <table class="w-full min-w-[860px] text-left text-xs">
        <thead class="text-surface-content/60">
          <tr>
            <th class="py-2 pr-3">{t("admin.metrics.columns.date")}</th>
            <th class="py-2 pr-3">{t("admin.metrics.columns.client")}</th>
            <th class="py-2 pr-3">{t("admin.metrics.columns.server")}</th>
            <th class="py-2 pr-3">{t("admin.metrics.columns.ip")}</th>
            <th class="py-2 pr-3">{t("admin.metrics.columns.metric")}</th>
            <th class="py-2 pr-3 text-right"
              >{t("admin.metrics.columns.value")}</th
            >
          </tr>
        </thead>
        <tbody class="text-surface-content/85">
          {#if $metrics.isLoading}
            <tr>
              <td colspan="6" class="py-4 text-center text-surface-content/60">
                {t("common.loading")}
              </td>
            </tr>
          {:else if ($metrics.data?.items ?? []).length === 0}
            <tr>
              <td colspan="6" class="py-4 text-center text-surface-content/60">
                {t("admin.metrics.empty")}
              </td>
            </tr>
          {:else}
            {#each $metrics.data?.items ?? [] as metric (metric.id)}
              <tr class="border-t border-surface-300/60">
                <td class="py-2 pr-3 text-surface-content/60">
                  {formatDate(metric.created_at)}
                </td>
                <td class="py-2 pr-3">
                  <div class="flex flex-col gap-0.5">
                    <span class="font-mono text-[11px]">{metric.client_ip}</span
                    >
                    {#if mapsUrl(metric)}
                      <a
                        href={mapsUrl(metric)}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label={t("admin.metrics.openInMaps")}
                        class="inline-flex items-center gap-1 text-surface-content/70 transition hover:text-primary"
                      >
                        <Globe size={12} aria-hidden="true" />
                        <span class="text-[11px]">
                          {metric.client_lat?.toFixed(3)},
                          {metric.client_lng?.toFixed(3)}
                        </span>
                      </a>
                    {:else}
                      <span
                        class="inline-flex items-center gap-1 text-surface-content/30"
                      >
                        <Globe size={12} aria-hidden="true" />
                      </span>
                    {/if}
                    <span class="text-[11px] text-surface-content/70">
                      {metric.client_isp ?? t("admin.metrics.ispFallback")}
                    </span>
                  </div>
                </td>
                <td class="py-2 pr-3">
                  <span class="font-mono">{metric.region_code}</span>
                  <span class="ml-1 text-surface-content/60"
                    >{metric.region_title}</span
                  >
                  <span class="ml-1 text-surface-content/40">
                    · {metric.server_alias || "—"}
                  </span>
                </td>
                <td class="py-2 pr-3 font-mono text-[11px]">
                  {metric.ip ?? "—"}
                </td>
                <td class="py-2 pr-3">
                  <code
                    class="rounded bg-surface-100/40 px-1.5 py-0.5 font-mono text-[11px]"
                  >
                    {metric.metric_alias}
                  </code>
                </td>
                <td class="py-2 pr-3 text-right font-mono">
                  {formatValue(metric.value)}
                </td>
              </tr>
            {/each}
          {/if}
        </tbody>
      </table>
    </div>

    {#if $metrics.data && $metrics.data.total > page.limit}
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
            disabled={page.offset === 0}
            class="rounded-pill border border-surface-300 px-3 py-1 transition hover:bg-surface-300/30 disabled:opacity-50"
          >
            {t("admin.probes.prev")}
          </button>
          <button
            type="button"
            onclick={goNext}
            disabled={page.offset + page.limit >= ($metrics.data?.total ?? 0)}
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
