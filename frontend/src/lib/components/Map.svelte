<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { onMount } from 'svelte';

  import { getOnlineProbes, type OnlineRegion } from '$lib/api/probes';
  import { ws } from '$lib/api/ws.svelte';
  import { locale, t } from '$lib/stores/locale.svelte';
  import regions from '../../../static/regions-layout.json';

  type RegionLayout = {
    code: string;
    label: string;
    title: string;
    row: number;
    col: number;
    db_code?: string;
  };

  const GRID_COLS = 19;
  const GRID_ROWS = 11;

  let { onSelect }: { onSelect?: (region: RegionLayout) => void } = $props();

  const online = createQuery({
    queryKey: ['probes', 'online'],
    queryFn: getOnlineProbes,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const live = $derived(
    ($online.data?.regions ?? {}) as Record<string, OnlineRegion>,
  );

  let selectedCode = $state<string | null>(null);

  function selectRegion(region: RegionLayout) {
    selectedCode = region.code;
    onSelect?.(region);
  }

  function speedClass(speed: number | null): string {
    if (speed === null) return 'bg-surface-300/60 text-surface-content';
    if (speed < 5) return 'bg-danger text-white';
    if (speed < 20) return 'bg-warning text-neutral-900';
    return 'bg-success text-neutral-950';
  }

  function tileStyle(region: RegionLayout): string {
    return `grid-column: ${region.col}; grid-row: ${region.row};`;
  }

  const legendItems = $derived([
    { key: 'fast' as const, swatch: 'bg-success' },
    { key: 'slow' as const, swatch: 'bg-warning' },
    { key: 'dead' as const, swatch: 'bg-danger' },
    {
      key: 'offline' as const,
      swatch: 'bg-surface-300/60 border border-surface-content/30',
    },
  ]);

  const selectedRegion = $derived(
    selectedCode
      ? ((regions as RegionLayout[]).find((r) => r.code === selectedCode) ??
          null)
      : null,
  );

  onMount(() =>
    ws.on((event) => {
      if (event.type === 'result.added' || event.type === 'region.updated')
        void $online.refetch();
    }),
  );
</script>

<section aria-labelledby="map-title" class="space-y-4">
  <div class="flex flex-wrap items-center justify-between gap-3">
    <h2 id="map-title" class="text-base font-semibold text-surface-content">
      {t('map.title')}
    </h2>
    <div
      class="flex flex-wrap items-center gap-3 text-xs text-surface-content/70"
      aria-label={t('map.title')}
    >
      {#each legendItems as item (item.key)}
        <span class="inline-flex items-center gap-1.5">
          <span class="inline-block h-3 w-3 rounded-sm {item.swatch}"></span>
          {t(`map.legend.${item.key}`)}
        </span>
      {/each}
    </div>
  </div>

  <div
    class="relative mx-auto w-full overflow-hidden rounded-card border border-surface-300 bg-surface-200/60 p-2 sm:p-3"
  >
    <div
      class="relative mx-auto w-full"
      style="aspect-ratio: {GRID_COLS} / {GRID_ROWS};"
    >
      <div
        class="grid h-full w-full gap-0.5 sm:gap-1"
        style="grid-template-columns: repeat({GRID_COLS}, minmax(0, 1fr)); grid-template-rows: repeat({GRID_ROWS}, minmax(0, 1fr));"
        role="grid"
        aria-label={t('map.title')}
      >
        {#each regions as region (region.code)}
          {@const lookupCode = region.db_code ?? region.code}
          {@const info = live[lookupCode]}
          {@const speed = info?.median_mbps ?? null}
          {@const isSelected = selectedCode === region.code}
          <button
            type="button"
            class="{speedClass(
              speed,
            )} flex items-center justify-center rounded-sm text-[9px] font-semibold leading-none transition outline-none select-none sm:text-[10px] {isSelected
              ? 'ring-2 ring-primary ring-offset-1 ring-offset-surface-200'
              : 'hover:opacity-90 focus-visible:ring-2 focus-visible:ring-primary'}"
            style={tileStyle(region)}
            aria-label={region.title}
            aria-pressed={isSelected}
            onclick={() => selectRegion(region)}
            onkeydown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectRegion(region);
              }
            }}
            title={region.title}
          >
            {region.label}
          </button>
        {/each}
      </div>
    </div>
  </div>

  {#if selectedRegion}
    {@const info = live[selectedRegion.db_code ?? selectedRegion.code]}
    <div
      class="rounded-card border border-surface-300 bg-surface-200/60 p-4 text-sm text-surface-content"
      aria-live="polite"
    >
      <div class="font-medium">
        {t('map.selected', {
          title: selectedRegion.title,
          code: selectedRegion.code,
        })}
      </div>
      <div class="mt-1 text-surface-content/70">
        {t('map.online', { count: info?.online ?? 0 })}
        {#if info?.median_mbps !== null && info?.median_mbps !== undefined}
          · {t('map.median', { value: info.median_mbps.toFixed(1) })}
        {:else}
          · {t('map.noResults')}
        {/if}
      </div>
    </div>
  {/if}

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</section>
