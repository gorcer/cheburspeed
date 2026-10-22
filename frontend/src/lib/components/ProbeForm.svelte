<script lang="ts">
  import { Link2 } from "@lucide/svelte";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { createQuery } from "@tanstack/svelte-query";
  import { untrack } from "svelte";

  import {
    listDestinations,
    type DestinationList,
  } from "$lib/api/destinations";
  import { createTask, ApiError, type TaskProtocol } from "$lib/api/tasks";
  import { currentTaskId } from "$lib/stores/currentTask.svelte";
  import { t, locale } from "$lib/stores/locale.svelte";

  let { onSubmitted }: { onSubmitted?: () => void } = $props();

  // An AmneziaWG config is an INI document with [Interface] and [Peer]
  // sections. The check is intentionally the same shape the server
  // applies (see _validate_awg_link) so the user is told about a broken
  // config before it is queued.
  const AWG_SECTION_PATTERN = /^\s*\[\s*(interface|peer)\s*\]/im;

  function parseInitialDestinationId(
    destinationParam: string | null,
    fallbackParam: string | null,
  ): number | null {
    const raw = destinationParam ?? fallbackParam;
    if (raw === null || raw === "") return null;
    const parsed = Number(raw);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  }

  function parseInitialLink(linkParam: string | null): string {
    if (linkParam === null) return "";
    return linkParam.trim().startsWith("vless://") ? linkParam : "";
  }

  const initialLink = $derived(
    parseInitialLink(page.url.searchParams.get("link")),
  );
  const initialDestinationId = $derived(
    parseInitialDestinationId(
      page.url.searchParams.get("destination"),
      page.url.searchParams.get("destination_id"),
    ),
  );

  let link = $state(untrack(() => initialLink));
  let protocol = $state<TaskProtocol>("vless");
  let destinationId = $state<number | null>(null);
  let submitting = $state(false);
  let errorMessage = $state<string | null>(null);
  let destinationSynced = $state(untrack(() => initialDestinationId) === null);

  const destinationsQuery = createQuery<
    DestinationList,
    Error,
    DestinationList,
    readonly ["destinations"]
  >({
    queryKey: ["destinations"] as const,
    queryFn: listDestinations,
    staleTime: 5 * 60_000,
  });

  const destinations = $derived($destinationsQuery.data?.items ?? []);
  const hasDestinations = $derived(destinations.length > 0);

  const isLinkValid = $derived(
    protocol === "amneziawg"
      ? AWG_SECTION_PATTERN.test(link)
      : link.trim().startsWith("vless://"),
  );
  const canSubmit = $derived(
    !submitting && isLinkValid && destinationId !== null,
  );

  $effect(() => {
    if (destinationSynced) return;
    if (initialDestinationId === null) {
      destinationSynced = true;
      return;
    }
    if (destinations.length === 0) return;
    if (
      destinations.some(
        (destination) => destination.id === initialDestinationId,
      )
    ) {
      destinationId = initialDestinationId;
    }
    destinationSynced = true;
  });

  async function handleSubmit(event: SubmitEvent) {
    event.preventDefault();
    errorMessage = null;
    const value = link.trim();
    if (protocol === "amneziawg") {
      if (!value) {
        errorMessage = t("form.awgRequired");
        return;
      }
      if (!AWG_SECTION_PATTERN.test(value)) {
        errorMessage = t("form.errorInvalidAwg");
        return;
      }
    } else if (!value.startsWith("vless://")) {
      errorMessage = t("form.errorInvalid");
      return;
    }
    if (destinationId === null) {
      errorMessage = t("form.destinationRequired");
      return;
    }
    submitting = true;
    try {
      const created = await createTask(value, {
        destinationId,
        protocol,
      });
      currentTaskId.set(created.id);
      const params = new URLSearchParams();
      // An AmneziaWG config carries the tunnel's PrivateKey, so it must
      // never reach the URL: it would end up in browser history and in
      // the nginx access log. Dropping the field also means the textarea
      // starts empty when the page is restored, which is what we want.
      if (protocol === "vless") {
        params.set("link", value);
      }
      params.set("destination", String(destinationId));
      await goto(`?${params.toString()}`, {
        replaceState: true,
        keepFocus: true,
        noScroll: true,
      });
      if (protocol === "amneziawg") {
        link = "";
      }
      onSubmitted?.();
    } catch (error) {
      if (error instanceof ApiError) {
        errorMessage = error.message;
      } else {
        errorMessage = t("form.errorNetwork");
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
  <h2
    id="probe-form-title"
    class="text-base font-semibold text-surface-content"
  >
    {t("form.title")}
  </h2>
  <p class="mt-1 text-sm text-surface-content/60">{t("form.description")}</p>

  <form onsubmit={handleSubmit} class="mt-4 flex flex-col gap-3">
    <label class="flex flex-col gap-1">
      <span class="text-xs text-surface-content/70">
        {t("form.destinationLabel")}
      </span>
      <select
        bind:value={destinationId}
        disabled={!hasDestinations}
        class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2.5 text-sm text-surface-content focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-60"
        aria-required="true"
      >
        <option value={null}>{t("form.destinationPlaceholder")}</option>
        {#each destinations as destination (destination.id)}
          <option value={destination.id}>{destination.name}</option>
        {/each}
      </select>
    </label>

    {#if $destinationsQuery.isError}
      <p class="text-xs text-danger" role="alert">
        {t("form.errorNetwork")}
      </p>
    {:else if !hasDestinations && !$destinationsQuery.isLoading}
      <p class="text-xs text-warning" role="alert">
        {t("form.destinationEmpty")}
      </p>
    {/if}

    <label class="flex flex-col gap-1">
      <span class="text-xs text-surface-content/70">
        {t("form.protocolLabel")}
      </span>
      <select
        bind:value={protocol}
        disabled={submitting}
        class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2.5 text-sm text-surface-content focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-60 sm:w-64"
      >
        <option value="vless">{t("form.protocolVless")}</option>
        <option value="amneziawg">{t("form.protocolAmneziawg")}</option>
      </select>
    </label>

    {#if protocol === "amneziawg"}
      <div class="flex flex-col gap-3 sm:flex-row sm:items-start">
        <label class="sr-only" for="probe-form-awg">
          {t("form.awgLabel")}
        </label>
        <div class="relative flex-1">
          <span
            class="pointer-events-none absolute inset-y-0 left-3 flex items-center text-surface-content/50"
          >
            <Link2 size={16} aria-hidden="true" />
          </span>
          <textarea
            id="probe-form-awg"
            bind:value={link}
            disabled={submitting}
            placeholder={t("form.awgPlaceholder")}
            rows={6}
            autocomplete="off"
            spellcheck="false"
            class="w-full resize-y rounded-card border border-surface-300 bg-surface-100 py-2.5 pl-9 pr-3 font-mono text-xs text-surface-content placeholder:text-surface-content/40 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-60"
            aria-invalid={errorMessage !== null}
          ></textarea>
        </div>
        <button
          type="submit"
          disabled={!canSubmit}
          class="inline-flex items-center justify-center rounded-pill bg-primary px-5 py-2.5 text-sm font-medium text-primary-content transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? t("form.submitting") : t("form.submit")}
        </button>
      </div>
    {:else}
      <div class="flex flex-col gap-3 sm:flex-row sm:items-start">
        <label class="sr-only" for="probe-form-link">
          {t("form.linkLabel")}
        </label>
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
            placeholder={t("form.linkPlaceholder")}
            autocomplete="off"
            spellcheck="false"
            class="w-full rounded-pill border border-surface-300 bg-surface-100 py-2.5 pl-9 pr-3 text-sm text-surface-content placeholder:text-surface-content/40 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-60"
            aria-invalid={errorMessage !== null}
          />
        </div>
        <button
          type="submit"
          disabled={!canSubmit}
          class="inline-flex items-center justify-center rounded-pill bg-primary px-5 py-2.5 text-sm font-medium text-primary-content transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? t("form.submitting") : t("form.submit")}
        </button>
      </div>
    {/if}
  </form>

  {#if errorMessage}
    <p class="mt-3 text-sm text-danger" role="alert">{errorMessage}</p>
  {/if}

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</section>
