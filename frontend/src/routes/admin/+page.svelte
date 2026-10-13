<script lang="ts">
  import { BarChart } from "layerchart";
  import { createQuery } from "@tanstack/svelte-query";
  import { scaleBand } from "d3-scale";

  import {
    type AdminStats,
    type AdminStatsBucket,
    type AdminStatsRegion,
    ApiError,
    getAdminStats,
  } from "$lib/api/admin";
  import { t, locale } from "$lib/stores/locale.svelte";

  const stats = createQuery<
    AdminStats,
    Error,
    AdminStats,
    readonly ["admin-stats"]
  >({
    queryKey: ["admin-stats"] as const,
    queryFn: getAdminStats,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const medianBars = $derived(
    ($stats.data?.median_per_region_7d ?? []) as AdminStatsRegion[],
  );
  const histogramBars = $derived(
    ($stats.data?.histogram ?? []) as AdminStatsBucket[],
  );

  const regionChartData = $derived(
    medianBars.map((region) => ({
      region_code: region.region_code,
      region_title: region.region_title,
      median_mbps: region.median_mbps ?? 0,
      sample_count: region.sample_count,
    })),
  );

  const histogramChartData = $derived(
    histogramBars.map((bucket) => ({
      label: bucketLabel(bucket, locale.code),
      min_mbps: bucket.min_mbps,
      count: bucket.count,
    })),
  );

  function bucketLabel(bucket: AdminStatsBucket, code: "ru" | "en"): string {
    if (bucket.max_mbps === null) {
      return `≥ ${bucket.min_mbps}`;
    }
    return `${bucket.min_mbps}–${bucket.max_mbps}`;
  }

  function formatNumber(value: number | null | undefined): string {
    if (value === null || value === undefined) return "—";
    if (value >= 100) return value.toFixed(0);
    if (value >= 10) return value.toFixed(1);
    return value.toFixed(2);
  }

  function formatLag(seconds: number | null): string {
    if (seconds === null) return t("admin.dashboard.archiveNever");
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
    if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
    return `${(seconds / 86400).toFixed(1)}d`;
  }

  const errorMessage = $derived(
    $stats.error instanceof ApiError ? $stats.error.message : null,
  );
</script>

<svelte:head>
  <title>XSpeed · {t("admin.tabs.dashboard")}</title>
</svelte:head>

<div class="space-y-5">
  <section
    aria-labelledby="dashboard-title"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <h2 id="dashboard-title" class="text-sm font-semibold text-surface-content">
      {t("admin.dashboard.totalsTitle")}
    </h2>
    {#if $stats.data}
      <dl
        class="mt-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-5"
        data-testid="totals"
      >
        <div
          class="rounded-card border border-surface-300 bg-surface-100/40 p-3"
        >
          <dt class="text-xs text-surface-content/60">
            {t("admin.dashboard.totals.regions")}
          </dt>
          <dd class="mt-1 text-lg font-semibold text-surface-content">
            {$stats.data.totals.regions}
          </dd>
        </div>
        <div
          class="rounded-card border border-surface-300 bg-surface-100/40 p-3"
        >
          <dt class="text-xs text-surface-content/60">
            {t("admin.dashboard.totals.probes")}
          </dt>
          <dd class="mt-1 text-lg font-semibold text-surface-content">
            {$stats.data.totals.probes}
          </dd>
        </div>
        <div
          class="rounded-card border border-surface-300 bg-surface-100/40 p-3"
        >
          <dt class="text-xs text-surface-content/60">
            {t("admin.dashboard.totals.tasks")}
          </dt>
          <dd class="mt-1 text-lg font-semibold text-surface-content">
            {$stats.data.totals.tasks}
          </dd>
        </div>
        <div
          class="rounded-card border border-surface-300 bg-surface-100/40 p-3"
        >
          <dt class="text-xs text-surface-content/60">
            {t("admin.dashboard.totals.results24h")}
          </dt>
          <dd class="mt-1 text-lg font-semibold text-surface-content">
            {$stats.data.totals.results_24h}
          </dd>
        </div>
        <div
          class="rounded-card border border-surface-300 bg-surface-100/40 p-3"
        >
          <dt class="text-xs text-surface-content/60">
            {t("admin.dashboard.totals.results7d")}
          </dt>
          <dd class="mt-1 text-lg font-semibold text-surface-content">
            {$stats.data.totals.results_7d}
          </dd>
        </div>
      </dl>
    {/if}
  </section>

  <section
    aria-labelledby="archive-lag"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5 text-sm"
  >
    <h2 id="archive-lag" class="text-sm font-semibold text-surface-content">
      {t("admin.dashboard.archiveTitle")}
    </h2>
    <p class="mt-2 text-surface-content/70">
      {#if $stats.data}
        <span class="font-mono text-base text-surface-content"
          >{formatLag($stats.data.archive_lag_seconds)}</span
        >
      {:else}
        —
      {/if}
    </p>
  </section>

  <section
    aria-labelledby="median-chart"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <header class="flex items-center justify-between">
      <h2 id="median-chart" class="text-sm font-semibold text-surface-content">
        {t("admin.dashboard.medianTitle")}
      </h2>
      <span class="text-xs text-surface-content/60"
        >{t("admin.dashboard.unit")}</span
      >
    </header>
    {#if $stats.isLoading}
      <p class="mt-4 text-sm text-surface-content/60">{t("common.loading")}</p>
    {:else if errorMessage}
      <p class="mt-4 text-sm text-danger" role="alert">{errorMessage}</p>
    {:else if regionChartData.length === 0}
      <p class="mt-4 text-sm text-surface-content/60">
        {t("admin.dashboard.empty")}
      </p>
    {:else}
      <div class="mt-4 h-72" data-testid="median-chart-svg">
        <BarChart
          data={regionChartData}
          x="region_code"
          xScale={scaleBand().padding(0.2)}
          y="median_mbps"
          yDomain={[0, null]}
          yNice
          axis="x"
          grid
          rule
          c="median_mbps"
          cRange={["hsl(188 86% 53% / 0.85)"]}
          padding={{ top: 16, right: 16, bottom: 32, left: 48 }}
          props={{
            bars: { radius: 4 },
            xAxis: {
              format: (v: string) => v,
            },
            yAxis: {
              format: (v: number) => `${v}`,
            },
          }}
        />
      </div>
    {/if}
  </section>

  <section
    aria-labelledby="histogram-chart"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <header class="flex items-center justify-between">
      <h2
        id="histogram-chart"
        class="text-sm font-semibold text-surface-content"
      >
        {t("admin.dashboard.histogramTitle")}
      </h2>
      <span class="text-xs text-surface-content/60"
        >{t("admin.dashboard.unitCount")}</span
      >
    </header>
    {#if $stats.isLoading}
      <p class="mt-4 text-sm text-surface-content/60">{t("common.loading")}</p>
    {:else if errorMessage}
      <p class="mt-4 text-sm text-danger" role="alert">{errorMessage}</p>
    {:else if histogramChartData.length === 0}
      <p class="mt-4 text-sm text-surface-content/60">
        {t("admin.dashboard.empty")}
      </p>
    {:else}
      <div class="mt-4 h-64" data-testid="histogram-chart-svg">
        <BarChart
          data={histogramChartData}
          x="label"
          xScale={scaleBand().padding(0.2)}
          y="count"
          yDomain={[0, null]}
          yNice
          axis="x"
          grid
          rule
          c="count"
          cRange={["hsl(45 95% 55% / 0.9)"]}
          padding={{ top: 16, right: 16, bottom: 40, left: 48 }}
          props={{
            bars: { radius: 4 },
          }}
        />
      </div>
    {/if}
  </section>

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</div>
