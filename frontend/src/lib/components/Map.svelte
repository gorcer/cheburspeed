<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { onMount } from 'svelte';

  import { getOnlineProbes, type OnlineRegion } from '$lib/api/probes';
  import { t, locale } from '$lib/stores/locale.svelte';
  import { ws } from '$lib/api/ws.svelte';
  import regions from '../../../static/regions-layout.json';

  type RegionLayout = { code: string; title: string; x: number; y: number };

  let { onSelect }: { onSelect?: (region: RegionLayout) => void } = $props();

  const online = createQuery({
    queryKey: ['probes', 'online'],
    queryFn: getOnlineProbes,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const live = $derived(($online.data?.regions ?? {}) as Record<string, OnlineRegion>);

  let selectedCode = $state<string | null>(null);

  function selectRegion(region: RegionLayout) {
    selectedCode = region.code;
    onSelect?.(region);
  }

  function speedColor(speed: number | null): string {
    if (speed === null) return '#262626';
    if (speed < 5) return '#ef4444';
    if (speed < 20) return '#eab308';
    return '#22c55e';
  }

  function onKey(event: KeyboardEvent, region: RegionLayout) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      selectRegion(region);
    }
  }

  onMount(() =>
    ws.on((event) => {
      if (event.type === 'result.added' || event.type === 'region.updated') void $online.refetch();
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
      <span class="inline-flex items-center gap-1.5">
        <span class="h-3 w-3 rounded-sm" style="background: #22c55e"></span>
        {t('map.legend.fast')}
      </span>
      <span class="inline-flex items-center gap-1.5">
        <span class="h-3 w-3 rounded-sm" style="background: #eab308"></span>
        {t('map.legend.slow')}
      </span>
      <span class="inline-flex items-center gap-1.5">
        <span class="h-3 w-3 rounded-sm" style="background: #ef4444"></span>
        {t('map.legend.dead')}
      </span>
      <span class="inline-flex items-center gap-1.5">
        <span class="h-3 w-3 rounded-sm" style="background: #262626"></span>
        {t('map.legend.offline')}
      </span>
    </div>
  </div>

  <div
    class="relative mx-auto w-full max-w-[1000px] overflow-hidden rounded-card border border-surface-300 bg-surface-200/60 p-2 sm:p-4"
  >
    <svg viewBox="0 0 1000 470" role="img" aria-label={t('map.title')} class="h-auto w-full">
      <g>
        {#each regions as region (region.code)}
          {@const speed = live[region.code]?.median_mbps ?? null}
          {@const onlineCount = live[region.code]?.online ?? 0}
          {@const fill = speedColor(speed)}
          {@const dim = speed === null}
          {@const isSelected = selectedCode === region.code}
          <g
            class="cursor-pointer transition-opacity duration-150 hover:opacity-100"
            class:opacity-60={dim}
            role="button"
            tabindex="0"
            aria-label={region.title}
            transform="translate({region.x} {region.y})"
            onclick={() => selectRegion(region)}
            onkeydown={(event) => onKey(event, region)}
          >
            <rect
              width="27"
              height="27"
              rx="2"
              fill={fill}
              stroke={isSelected ? '#22d3ee' : '#525252'}
              stroke-width={isSelected ? 1.4 : 0.7}
            ></rect>
            <text
              x="13.5"
              y="13.5"
              text-anchor="middle"
              dominant-baseline="central"
              font-size="9.5"
              font-weight="600"
              class="pointer-events-none select-none"
              fill="#fafafa"
            >
              {region.code}
            </text>
          </g>
        {/each}
      </g>
    </svg>
  </div>

  {#if selectedCode}
    {@const region = regions.find((r) => r.code === selectedCode)}
    {#if region}
      {@const info = live[region.code]}
      <div
        class="rounded-card border border-surface-300 bg-surface-200/60 p-4 text-sm text-surface-content"
        aria-live="polite"
      >
        <div class="font-medium">
          {t('map.selected', { title: region.title, code: region.code })}
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
  {/if}

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</section>
