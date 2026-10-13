<script lang="ts">
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';

  import { auth } from '$lib/stores/auth.svelte';
  import { t, locale } from '$lib/stores/locale.svelte';

  let ready = $state(false);

  onMount(() => {
    ready = true;
    if (!auth.isAuthenticated) {
      goto('/admin/login');
    }
  });

  function signOut() {
    auth.clear();
    goto('/admin/login');
  }
</script>

<svelte:head>
  <title>XSpeed · {t('admin.dashboard')}</title>
</svelte:head>

<section
  aria-labelledby="admin-title"
  class="rounded-card border border-surface-300 bg-surface-200/60 p-6"
>
  <div class="flex items-center justify-between">
    <h1 id="admin-title" class="text-base font-semibold text-surface-content">
      {t('admin.dashboard')}
    </h1>
    {#if ready && auth.isAuthenticated}
      <button
        type="button"
        onclick={signOut}
        class="inline-flex items-center rounded-pill border border-surface-300 px-3 py-1.5 text-xs text-surface-content/70 transition hover:bg-surface-300/30"
      >
        {t('admin.logout')}
      </button>
    {/if}
  </div>
  <p class="mt-3 text-sm text-surface-content/60">
    {t('admin.comingSoon')}
  </p>

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</section>