<script lang="ts">
  import DocsEndpoint from "$lib/components/DocsEndpoint.svelte";
  import { t, locale } from "$lib/stores/locale.svelte";

  const endpoints = [
    { key: "createTask" },
    { key: "getTask" },
    { key: "listTasks" },
    { key: "listRegions" },
    { key: "onlineProbes" },
    { key: "listDestinations" },
    { key: "urlParams" },
  ] as const;

  const events = ["taskAssigned", "online", "result"] as const;

  const limits = ["createTask", "getTask"] as const;
</script>

<svelte:head>
  <title>CheburSpeed · {t("docs.title")}</title>
</svelte:head>

<article
  class="space-y-6 rounded-card border border-surface-300 bg-surface-200/60 p-6"
>
  <header class="space-y-2">
    <h1 class="text-lg font-semibold text-surface-content">
      {t("docs.title")}
    </h1>
    <p class="text-sm text-surface-content/70">{t("docs.intro")}</p>
  </header>

  <section class="space-y-3">
    <h2
      class="text-sm font-semibold uppercase tracking-wide text-surface-content/60"
    >
      {t("docs.apiTitle")}
    </h2>
    <ul class="space-y-2 text-sm">
      {#each endpoints as endpoint (endpoint.key)}
        <li>
          <DocsEndpoint
            title={t(`docs.endpoints.${endpoint.key}.title`)}
            description={t(`docs.endpoints.${endpoint.key}.description`)}
            request={t(`docs.examples.${endpoint.key}.request`)}
            response={t(`docs.examples.${endpoint.key}.response`)}
            status={t(`docs.examples.${endpoint.key}.status`)}
          />
        </li>
      {/each}
    </ul>
  </section>

  <section class="space-y-3">
    <h2
      class="text-sm font-semibold uppercase tracking-wide text-surface-content/60"
    >
      {t("docs.wsTitle")}
    </h2>
    <p class="text-sm text-surface-content/70">{t("docs.wsDescription")}</p>
    <ul class="list-disc space-y-1 pl-5 text-sm text-surface-content/70">
      {#each events as event (event)}
        <li>{t(`docs.events.${event}`)}</li>
      {/each}
    </ul>
  </section>

  <section class="space-y-3">
    <h2
      class="text-sm font-semibold uppercase tracking-wide text-surface-content/60"
    >
      {t("docs.limitsTitle")}
    </h2>
    <ul class="list-disc space-y-1 pl-5 text-sm text-surface-content/70">
      {#each limits as limit (limit)}
        <li>{t(`docs.limits.${limit}`)}</li>
      {/each}
    </ul>
  </section>

  <span class="sr-only" aria-live="polite">{locale.code}</span>
</article>
