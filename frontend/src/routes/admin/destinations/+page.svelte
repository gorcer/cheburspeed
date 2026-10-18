<script lang="ts">
  import { RefreshCw, Trash2 } from "@lucide/svelte";
  import { createQuery, useQueryClient } from "@tanstack/svelte-query";
  import { derived, writable } from "svelte/store";

  import {
    type AdminDestination,
    type AdminDestinationList,
    ApiError,
    createAdminDestination,
    deleteAdminDestination,
    listAdminDestinations,
    updateAdminDestination,
  } from "$lib/api/destinations";
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

  const destinationsOptions = derived(pageStore, ($page) => ({
    queryKey: ["admin-destinations", $page] as const,
    queryFn: () => listAdminDestinations($page),
    refetchInterval: 30_000,
    staleTime: 10_000,
  }));
  const destinations = createQuery<
    AdminDestinationList,
    Error,
    AdminDestinationList,
    readonly ["admin-destinations", { limit: number; offset: number }]
  >(destinationsOptions);

  const queryClient = useQueryClient();

  let formName = $state("");
  let formUrl = $state("");
  let formError = $state<string | null>(null);
  let formSubmitting = $state(false);
  let editingId = $state<number | null>(null);
  let editName = $state("");
  let editUrl = $state("");

  function resetForm() {
    formName = "";
    formUrl = "";
    formError = null;
  }

  async function handleCreate(event: SubmitEvent) {
    event.preventDefault();
    formError = null;
    formSubmitting = true;
    try {
      await createAdminDestination({
        name: formName.trim(),
        url: formUrl.trim(),
      });
      resetForm();
      void queryClient.invalidateQueries({ queryKey: ["admin-destinations"] });
    } catch (err) {
      formError = err instanceof Error ? err.message : t("common.error");
    } finally {
      formSubmitting = false;
    }
  }

  function startEdit(destination: AdminDestination) {
    editingId = destination.id;
    editName = destination.name;
    editUrl = destination.url;
  }

  function cancelEdit() {
    editingId = null;
    editName = "";
    editUrl = "";
  }

  async function saveEdit(destination: AdminDestination) {
    try {
      await updateAdminDestination(destination.id, {
        name: editName.trim(),
        url: editUrl.trim(),
      });
      editingId = null;
      void queryClient.invalidateQueries({ queryKey: ["admin-destinations"] });
    } catch (err) {
      formError = err instanceof Error ? err.message : t("common.error");
    }
  }

  async function handleDelete(destination: AdminDestination) {
    if (
      !confirm(
        t("admin.destinations.confirmDelete", { name: destination.name }),
      )
    ) {
      return;
    }
    try {
      await deleteAdminDestination(destination.id);
      void queryClient.invalidateQueries({ queryKey: ["admin-destinations"] });
    } catch (err) {
      if (err instanceof ApiError) {
        formError = err.message;
      } else {
        formError = err instanceof Error ? err.message : t("common.error");
      }
    }
  }

  function refresh() {
    void $destinations.refetch();
  }

  function goPrev() {
    if (page.offset === 0) return;
    page = { ...page, offset: Math.max(0, page.offset - page.limit) };
  }

  function goNext() {
    const total = $destinations.data?.total ?? 0;
    if (page.offset + page.limit >= total) return;
    page = { ...page, offset: page.offset + page.limit };
  }

  const currentPageNumber = $derived(Math.floor(page.offset / page.limit) + 1);
  const totalPages = $derived(
    Math.max(1, Math.ceil(($destinations.data?.total ?? 0) / page.limit)),
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
</script>

<svelte:head>
  <title>CheburSpeed · {t("admin.tabs.destinations")}</title>
</svelte:head>

<div class="space-y-5">
  <section
    aria-labelledby="destination-form-title"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <h2
      id="destination-form-title"
      class="text-sm font-semibold text-surface-content"
    >
      {t("admin.destinations.createTitle")}
    </h2>
    <form onsubmit={handleCreate} class="mt-3 grid gap-3 md:grid-cols-3">
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.destinations.name")}</span>
        <input
          type="text"
          bind:value={formName}
          required
          maxlength={128}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label class="text-xs text-surface-content/70 md:col-span-2">
        <span class="mb-1 block">{t("admin.destinations.url")}</span>
        <input
          type="url"
          bind:value={formUrl}
          required
          maxlength={2048}
          placeholder="https://speed.example.com/__down?bytes=100000000"
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm placeholder:text-surface-content/40 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
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
          {formSubmitting
            ? t("common.loading")
            : t("admin.destinations.submit")}
        </button>
      </div>
    </form>
  </section>

  <section
    aria-labelledby="destination-list-title"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <header class="flex flex-wrap items-center justify-between gap-2">
      <h2
        id="destination-list-title"
        class="text-sm font-semibold text-surface-content"
      >
        {t("admin.destinations.listTitle")}
        {#if $destinations.data}
          <span class="ml-2 text-xs text-surface-content/60">
            ({$destinations.data.total})
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
      <table class="w-full min-w-[640px] text-left text-xs">
        <thead class="text-surface-content/60">
          <tr>
            <th class="py-2 pr-3">{t("admin.destinations.columns.id")}</th>
            <th class="py-2 pr-3">{t("admin.destinations.columns.name")}</th>
            <th class="py-2 pr-3">{t("admin.destinations.columns.url")}</th>
            <th class="py-2 pr-3">{t("admin.destinations.columns.created")}</th>
            <th class="py-2 pr-3 text-right"
              >{t("admin.destinations.columns.actions")}</th
            >
          </tr>
        </thead>
        <tbody class="text-surface-content/85">
          {#if $destinations.isLoading}
            <tr>
              <td colspan="5" class="py-4 text-center text-surface-content/60">
                {t("common.loading")}
              </td>
            </tr>
          {:else if ($destinations.data?.items ?? []).length === 0}
            <tr>
              <td colspan="5" class="py-4 text-center text-surface-content/60">
                {t("admin.destinations.empty")}
              </td>
            </tr>
          {:else}
            {#each $destinations.data?.items ?? [] as destination (destination.id)}
              <tr class="border-t border-surface-300/60">
                <td class="py-2 pr-3 font-mono">{destination.id}</td>
                <td class="py-2 pr-3">
                  {#if editingId === destination.id}
                    <input
                      type="text"
                      bind:value={editName}
                      required
                      maxlength={128}
                      class="w-40 rounded-pill border border-surface-300 bg-surface-100 px-2 py-1 text-xs"
                    />
                  {:else}
                    {destination.name}
                  {/if}
                </td>
                <td class="py-2 pr-3">
                  {#if editingId === destination.id}
                    <input
                      type="url"
                      bind:value={editUrl}
                      required
                      maxlength={2048}
                      class="w-full rounded-pill border border-surface-300 bg-surface-100 px-2 py-1 text-xs"
                    />
                  {:else}
                    <code
                      class="rounded bg-surface-100/40 px-1.5 py-0.5 font-mono text-[11px]"
                    >
                      {destination.url}
                    </code>
                  {/if}
                </td>
                <td class="py-2 pr-3 text-surface-content/60">
                  {formatDate(destination.created_at)}
                </td>
                <td class="py-2 pr-3 text-right">
                  {#if editingId === destination.id}
                    <div class="inline-flex items-center gap-2">
                      <button
                        type="button"
                        onclick={() => saveEdit(destination)}
                        class="text-xs text-primary hover:underline"
                      >
                        {t("admin.destinations.save")}
                      </button>
                      <button
                        type="button"
                        onclick={cancelEdit}
                        class="text-xs text-surface-content/60 hover:underline"
                      >
                        {t("admin.destinations.cancel")}
                      </button>
                    </div>
                  {:else}
                    <div class="inline-flex items-center gap-2">
                      <button
                        type="button"
                        onclick={() => startEdit(destination)}
                        class="text-xs text-surface-content/70 hover:text-primary"
                      >
                        {t("admin.destinations.edit")}
                      </button>
                      <button
                        type="button"
                        onclick={() => handleDelete(destination)}
                        class="text-xs text-danger hover:underline"
                        aria-label={t("admin.destinations.delete")}
                      >
                        <Trash2 size={12} aria-hidden="true" />
                      </button>
                    </div>
                  {/if}
                </td>
              </tr>
            {/each}
          {/if}
        </tbody>
      </table>
    </div>

    {#if $destinations.data && $destinations.data.total > page.limit}
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
            disabled={page.offset + page.limit >=
              ($destinations.data?.total ?? 0)}
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
