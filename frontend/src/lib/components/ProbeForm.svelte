<script lang="ts">
  import { Link2 } from '@lucide/svelte';

  import { createTask, ApiError } from '$lib/api/tasks';
  import { currentTask } from '$lib/stores/currentTask.svelte';
  import { t, locale } from '$lib/stores/locale.svelte';

  let { onSubmitted }: { onSubmitted?: () => void } = $props();

  let link = $state('');
  let submitting = $state(false);
  let errorMessage = $state<string | null>(null);

  const isValid = $derived(link.trim().startsWith('vless://'));

  async function handleSubmit(event: SubmitEvent) {
    event.preventDefault();
    errorMessage = null;
    const value = link.trim();
    if (!value.startsWith('vless://')) {
      errorMessage = t('form.errorInvalid');
      return;
    }
    submitting = true;
    try {
      const created = await createTask(value);
      currentTask.set(created.id);
      onSubmitted?.();
    } catch (error) {
      if (error instanceof ApiError) {
        errorMessage = error.message;
      } else {
        errorMessage = t('form.errorNetwork');
      }
    } finally {
      submitting = false;
    }
  }
</script>

<section
  aria-labelledby="probe-form-title"
  class="rounded-card border border-surface-300 bg-surface-200/60 p-5 shadow-sm"
>
  <h2 id="probe-form-title" class="text-base font-semibold text-surface-content">
    {t('form.title')}
  </h2>
  <p class="mt-1 text-sm text-surface-content/60">{t('form.description')}</p>

  <form onsubmit={handleSubmit} class="mt-4 flex flex-col gap-3 sm:flex-row sm:items-start">
    <label class="sr-only" for="probe-form-link">{t('form.title')}</label>
    <div class="relative flex-1">
      <span
        class="pointer-events-none absolute inset-y-0 left-3 flex items-center text-surface-content/50"
      >
        <Link2 size={16} aria-hidden="true" />
      </span>
      <input
        id="probe-form-link"
        type="url"
        bind:value={link}
        disabled={submitting}
        placeholder={t('form.linkPlaceholder')}
        autocomplete="off"
        spellcheck="false"
        class="w-full rounded-pill border border-surface-300 bg-surface-100 py-2.5 pl-9 pr-3 text-sm text-surface-content placeholder:text-surface-content/40 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-60"
        aria-invalid={errorMessage !== null}
      />
    </div>
    <button
      type="submit"
      disabled={submitting || !isValid}
      class="inline-flex items-center justify-center rounded-pill bg-primary px-5 py-2.5 text-sm font-medium text-primary-content transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {submitting ? t('form.submitting') : t('form.submit')}
    </button>
  </form>

  {#if errorMessage}
    <p class="mt-3 text-sm text-danger" role="alert">{errorMessage}</p>
  {/if}

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</section>