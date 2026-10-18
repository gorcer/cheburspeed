<script lang="ts">
  import { Check, Copy, RefreshCw, Trash2 } from "@lucide/svelte";
  import { createQuery, useQueryClient } from "@tanstack/svelte-query";
  import { derived, writable } from "svelte/store";

  import {
    type AdminProbe,
    type AdminProbeList,
    ApiError,
    createAdminProbe,
    deleteAdminProbe,
    listAdminProbes,
    updateAdminProbe,
  } from "$lib/api/admin";
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

  const probesOptions = derived(pageStore, ($page) => ({
    queryKey: ["admin-probes", $page] as const,
    queryFn: () => listAdminProbes($page),
    refetchInterval: 30_000,
    staleTime: 10_000,
  }));
  const probes = createQuery<
    AdminProbeList,
    Error,
    AdminProbeList,
    readonly ["admin-probes", { limit: number; offset: number }]
  >(probesOptions);

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

  let formAlias = $state("");
  let formRegionId = $state<number | null>(null);
  let formIsPublic = $state(true);
  let formToken = $state("");
  let formError = $state<string | null>(null);
  let formSubmitting = $state(false);
  let editingId = $state<number | null>(null);
  let editAlias = $state("");
  let editIsPublic = $state(true);
  let copyState = $state<Record<number, "copied" | "failed" | null>>({});

  function resetForm() {
    formAlias = "";
    formRegionId = null;
    formIsPublic = true;
    formToken = "";
    formError = null;
  }

  async function handleCreate(event: SubmitEvent) {
    event.preventDefault();
    if (formRegionId === null) {
      formError = t("admin.probes.regionRequired");
      return;
    }
    formError = null;
    formSubmitting = true;
    try {
      await createAdminProbe({
        alias: formAlias.trim(),
        region_id: formRegionId,
        is_public: formIsPublic,
        token: formToken.trim() || undefined,
      });
      resetForm();
      void queryClient.invalidateQueries({ queryKey: ["admin-probes"] });
    } catch (err) {
      formError = err instanceof Error ? err.message : t("common.error");
    } finally {
      formSubmitting = false;
    }
  }

  function startEdit(probe: AdminProbe) {
    editingId = probe.id;
    editAlias = probe.alias;
    editIsPublic = probe.is_public;
  }

  function cancelEdit() {
    editingId = null;
    editAlias = "";
    editIsPublic = true;
  }

  async function saveEdit(probe: AdminProbe) {
    try {
      await updateAdminProbe(probe.id, {
        alias: editAlias.trim(),
        is_public: editIsPublic,
      });
      editingId = null;
      void queryClient.invalidateQueries({ queryKey: ["admin-probes"] });
    } catch (err) {
      formError = err instanceof Error ? err.message : t("common.error");
    }
  }

  async function handleDelete(probe: AdminProbe) {
    if (
      !confirm(
        t("admin.probes.confirmDelete", {
          alias: probe.alias || probe.token.slice(0, 8),
        }),
      )
    ) {
      return;
    }
    try {
      await deleteAdminProbe(probe.id);
      void queryClient.invalidateQueries({ queryKey: ["admin-probes"] });
    } catch (err) {
      if (err instanceof ApiError) {
        formError = err.message;
      } else {
        formError = err instanceof Error ? err.message : t("common.error");
      }
    }
  }

  async function copyViaExecCommand(text: string): Promise<boolean> {
    if (typeof document === "undefined") return false;
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.top = "0";
    textarea.style.left = "-9999px";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    const previousSelection = document.getSelection();
    const savedRange =
      previousSelection && previousSelection.rangeCount > 0
        ? previousSelection.getRangeAt(0)
        : null;
    textarea.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    document.body.removeChild(textarea);
    if (savedRange && previousSelection) {
      previousSelection.removeAllRanges();
      previousSelection.addRange(savedRange);
    }
    return ok;
  }

  async function copyToken(probe: AdminProbe) {
    let ok = false;
    if (
      typeof navigator !== "undefined" &&
      navigator.clipboard &&
      typeof navigator.clipboard.writeText === "function" &&
      window.isSecureContext
    ) {
      try {
        await navigator.clipboard.writeText(probe.token);
        ok = true;
      } catch {
        ok = false;
      }
    }
    if (!ok) {
      ok = await copyViaExecCommand(probe.token);
    }
    copyState = { ...copyState, [probe.id]: ok ? "copied" : "failed" };
    setTimeout(() => {
      if (copyState[probe.id] === (ok ? "copied" : "failed")) {
        const next = { ...copyState };
        delete next[probe.id];
        copyState = next;
      }
    }, 1800);
  }

  function refresh() {
    void $probes.refetch();
  }

  function goPrev() {
    if (page.offset === 0) return;
    page = { ...page, offset: Math.max(0, page.offset - page.limit) };
  }

  function goNext() {
    const total = $probes.data?.total ?? 0;
    if (page.offset + page.limit >= total) return;
    page = { ...page, offset: page.offset + page.limit };
  }

  const currentPageNumber = $derived(Math.floor(page.offset / page.limit) + 1);
  const totalPages = $derived(
    Math.max(1, Math.ceil(($probes.data?.total ?? 0) / page.limit)),
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
  <title>CheburSpeed · {t("admin.tabs.probes")}</title>
</svelte:head>

<div class="space-y-5">
  <section
    aria-labelledby="probe-form-title"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <h2
      id="probe-form-title"
      class="text-sm font-semibold text-surface-content"
    >
      {t("admin.probes.createTitle")}
    </h2>
    <form onsubmit={handleCreate} class="mt-3 grid gap-3 md:grid-cols-4">
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.probes.alias")}</span>
        <input
          type="text"
          bind:value={formAlias}
          maxlength={255}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label class="text-xs text-surface-content/70">
        <span class="mb-1 block">{t("admin.probes.region")}</span>
        <select
          bind:value={formRegionId}
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
        <span class="mb-1 block">{t("admin.probes.token")}</span>
        <input
          type="text"
          bind:value={formToken}
          minlength={8}
          maxlength={128}
          placeholder={t("admin.probes.tokenAuto")}
          class="w-full rounded-pill border border-surface-300 bg-surface-100 px-3 py-2 text-sm placeholder:text-surface-content/40 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <label
        class="inline-flex items-center gap-2 self-end pb-1 text-xs text-surface-content/70"
      >
        <input
          type="checkbox"
          bind:checked={formIsPublic}
          class="size-4 rounded border border-surface-300 bg-surface-100"
        />
        <span>{t("admin.probes.isPublic")}</span>
      </label>
      {#if formError}
        <p class="text-xs text-danger md:col-span-4" role="alert">
          {formError}
        </p>
      {/if}
      <div class="md:col-span-4">
        <button
          type="submit"
          disabled={formSubmitting}
          class="inline-flex items-center rounded-pill bg-primary px-4 py-2 text-sm font-medium text-primary-content transition hover:brightness-110 disabled:opacity-60"
        >
          {formSubmitting ? t("common.loading") : t("admin.probes.submit")}
        </button>
      </div>
    </form>
  </section>

  <section
    aria-labelledby="probe-list-title"
    class="rounded-card border border-surface-300 bg-surface-200/60 p-5"
  >
    <header class="flex flex-wrap items-center justify-between gap-2">
      <h2
        id="probe-list-title"
        class="text-sm font-semibold text-surface-content"
      >
        {t("admin.probes.listTitle")}
        {#if $probes.data}
          <span class="ml-2 text-xs text-surface-content/60">
            ({$probes.data.total})
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
            <th class="py-2 pr-3">{t("admin.probes.columns.id")}</th>
            <th class="py-2 pr-3">{t("admin.probes.columns.alias")}</th>
            <th class="py-2 pr-3">{t("admin.probes.columns.region")}</th>
            <th class="py-2 pr-3">{t("admin.probes.columns.token")}</th>
            <th class="py-2 pr-3">{t("admin.probes.columns.public")}</th>
            <th class="py-2 pr-3">{t("admin.probes.columns.lastSeen")}</th>
            <th class="py-2 pr-3 text-right"
              >{t("admin.probes.columns.actions")}</th
            >
          </tr>
        </thead>
        <tbody class="text-surface-content/85">
          {#if $probes.isLoading}
            <tr>
              <td colspan="7" class="py-4 text-center text-surface-content/60">
                {t("common.loading")}
              </td>
            </tr>
          {:else if ($probes.data?.items ?? []).length === 0}
            <tr>
              <td colspan="7" class="py-4 text-center text-surface-content/60">
                {t("admin.probes.empty")}
              </td>
            </tr>
          {:else}
            {#each $probes.data?.items ?? [] as probe (probe.id)}
              <tr class="border-t border-surface-300/60">
                <td class="py-2 pr-3 font-mono">{probe.id}</td>
                <td class="py-2 pr-3">
                  {#if editingId === probe.id}
                    <input
                      type="text"
                      bind:value={editAlias}
                      maxlength={255}
                      class="w-32 rounded-pill border border-surface-300 bg-surface-100 px-2 py-1 text-xs"
                    />
                  {:else}
                    {probe.alias || "—"}
                  {/if}
                </td>
                <td class="py-2 pr-3">
                  <span class="font-mono">{probe.region_code}</span>
                  <span class="ml-1 text-surface-content/60"
                    >{probe.region_title}</span
                  >
                </td>
                <td class="py-2 pr-3">
                  <div class="flex items-center gap-1.5">
                    <code
                      class="rounded bg-surface-100/40 px-1.5 py-0.5 font-mono text-[11px]"
                    >
                      {probe.token.slice(0, 12)}…
                    </code>
                    <button
                      type="button"
                      onclick={() => copyToken(probe)}
                      class="text-surface-content/60 transition hover:text-primary disabled:cursor-default disabled:opacity-100"
                      aria-label={t("admin.probes.copyToken")}
                      title={copyState[probe.id] === "failed"
                        ? probe.token
                        : undefined}
                    >
                      {#if copyState[probe.id] === "copied"}
                        <Check
                          size={12}
                          class="text-success"
                          aria-hidden="true"
                        />
                        <span class="sr-only"
                          >{t("admin.probes.copyTokenCopied")}</span
                        >
                      {:else if copyState[probe.id] === "failed"}
                        <Copy
                          size={12}
                          class="text-danger"
                          aria-hidden="true"
                        />
                        <span class="sr-only"
                          >{t("admin.probes.copyTokenFailed")}</span
                        >
                      {:else}
                        <Copy size={12} aria-hidden="true" />
                      {/if}
                    </button>
                  </div>
                </td>
                <td class="py-2 pr-3">
                  {#if editingId === probe.id}
                    <input
                      type="checkbox"
                      bind:checked={editIsPublic}
                      class="size-3.5"
                    />
                  {:else}
                    {probe.is_public
                      ? t("admin.probes.yes")
                      : t("admin.probes.no")}
                  {/if}
                </td>
                <td class="py-2 pr-3 text-surface-content/60">
                  {formatDate(probe.last_seen_at)}
                </td>
                <td class="py-2 pr-3 text-right">
                  {#if editingId === probe.id}
                    <div class="inline-flex items-center gap-2">
                      <button
                        type="button"
                        onclick={() => saveEdit(probe)}
                        class="text-xs text-primary hover:underline"
                      >
                        {t("admin.probes.save")}
                      </button>
                      <button
                        type="button"
                        onclick={cancelEdit}
                        class="text-xs text-surface-content/60 hover:underline"
                      >
                        {t("admin.probes.cancel")}
                      </button>
                    </div>
                  {:else}
                    <div class="inline-flex items-center gap-2">
                      <button
                        type="button"
                        onclick={() => startEdit(probe)}
                        class="text-xs text-surface-content/70 hover:text-primary"
                      >
                        {t("admin.probes.edit")}
                      </button>
                      <button
                        type="button"
                        onclick={() => handleDelete(probe)}
                        class="text-xs text-danger hover:underline"
                        aria-label={t("admin.probes.delete")}
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

    {#if $probes.data && $probes.data.total > page.limit}
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
            disabled={page.offset + page.limit >= ($probes.data?.total ?? 0)}
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
