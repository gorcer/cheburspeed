<script lang="ts">
  import { Globe, Languages, ShieldCheck } from '@lucide/svelte';
  import { page } from '$app/state';

  import { locale } from '$lib/stores/locale.svelte';
  import { t } from '$lib/stores/locale.svelte';
  import StatusDot from './StatusDot.svelte';

  let { children } = $props();

  const navItems = $derived([
    { href: '/', label: t('nav.home') },
    { href: '/docs', label: t('nav.docs') },
    { href: '/admin', label: t('nav.admin') },
  ]);
</script>

<div class="min-h-screen bg-surface-100 text-surface-content">
  <header class="border-b border-surface-300 bg-surface-100/95 backdrop-blur">
    <div class="mx-auto flex max-w-6xl flex-wrap items-center gap-4 px-4 py-3">
      <a href="/" class="inline-flex items-center gap-2 text-base font-semibold">
        <span
          class="inline-flex h-8 w-8 items-center justify-center rounded-card bg-primary/15 text-primary"
        >
          <ShieldCheck size={18} aria-hidden="true" />
        </span>
        <span>{t('app.title')}</span>
      </a>
      <nav class="flex flex-1 items-center gap-1 text-sm">
        {#each navItems as item (item.href)}
          {@const isActive = item.href === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(item.href)}
          <a
            href={item.href}
            class={`rounded-pill px-3 py-1.5 transition ${
              isActive
                ? 'bg-surface-300/60 text-surface-content'
                : 'text-surface-content/70 hover:bg-surface-300/40 hover:text-surface-content'
            }`}
          >
            {item.label}
          </a>
        {/each}
      </nav>
      <div class="flex items-center gap-3">
        <StatusDot />
        <button
          type="button"
          onclick={() => locale.toggle()}
          class="inline-flex items-center gap-1.5 rounded-pill border border-surface-300 px-3 py-1.5 text-xs uppercase tracking-wide text-surface-content/70 transition hover:bg-surface-300/30"
          aria-label={t('nav.language')}
        >
          <Languages size={14} aria-hidden="true" />
          <span>{locale.code}</span>
        </button>
      </div>
    </div>
    <p class="mx-auto max-w-6xl px-4 pb-3 text-xs text-surface-content/50">
      <Globe size={12} aria-hidden="true" class="mr-1 inline" />
      {t('app.subtitle')}
    </p>
  </header>

  <main class="mx-auto max-w-6xl space-y-6 px-4 py-6">
    {@render children()}
  </main>

  <footer class="mx-auto max-w-6xl px-4 py-6 text-center text-surface-content/40">
    <span class="text-xs">© XSpeed</span>
  </footer>
</div>