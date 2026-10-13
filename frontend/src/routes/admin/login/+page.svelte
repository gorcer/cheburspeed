<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/state';

  import { t, locale } from '$lib/stores/locale.svelte';
  import { auth } from '$lib/stores/auth.svelte';

  let email = $state('');
  let password = $state('');
  let error = $state<string | null>(null);
  let submitting = $state(false);

  $effect(() => {
    if (auth.isAuthenticated && page.url.pathname !== '/admin') {
      goto('/admin');
    }
  });

  function fakeLogin() {
    error = null;
    if (!email.trim() || !password) {
      error = t('admin.unauthorized');
      return;
    }
    submitting = true;
    setTimeout(() => {
      auth.set(`fake-${email.trim()}`);
      submitting = false;
      goto('/admin');
    }, 200);
  }
</script>

<svelte:head>
  <title>XSpeed · {t('admin.loginTitle')}</title>
</svelte:head>

<section
  aria-labelledby="login-title"
  class="mx-auto max-w-md rounded-card border border-surface-300 bg-surface-200/60 p-6"
>
  <h1 id="login-title" class="text-base font-semibold text-surface-content">
    {t('admin.loginTitle')}
  </h1>
  <p class="mt-1 text-xs text-surface-content/60">
    {t('admin.comingSoon')}
  </p>

  <form
    onsubmit={(event) => {
      event.preventDefault();
      fakeLogin();
    }}
    class="mt-5 space-y-3"
  >
    <label class="block text-xs text-surface-content/70">
      <span class="sr-only">{t('admin.emailPlaceholder')}</span>
      <input
        type="email"
        bind:value={email}
        autocomplete="email"
        placeholder={t('admin.emailPlaceholder')}
        class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm placeholder:text-surface-content/40 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
      />
    </label>
    <label class="block text-xs text-surface-content/70">
      <span class="sr-only">{t('admin.passwordPlaceholder')}</span>
      <input
        type="password"
        bind:value={password}
        autocomplete="current-password"
        placeholder={t('admin.passwordPlaceholder')}
        class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm placeholder:text-surface-content/40 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
      />
    </label>
    {#if error}
      <p class="text-xs text-danger" role="alert">{error}</p>
    {/if}
    <button
      type="submit"
      disabled={submitting}
      class="inline-flex w-full items-center justify-center rounded-pill bg-primary px-4 py-2 text-sm font-medium text-primary-content transition hover:brightness-110 disabled:opacity-60"
    >
      {t('admin.submit')}
    </button>
  </form>

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</section>