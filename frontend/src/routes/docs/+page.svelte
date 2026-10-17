<script lang="ts">
  import { t, locale } from "$lib/stores/locale.svelte";

  const endpoints = [
    { key: "createTask" },
    { key: "getTask" },
    { key: "listTasks" },
    { key: "listRegions" },
    { key: "onlineProbes" },
    { key: "listDestinations" },
  ] as const;

  const adminEndpoints = [
    { key: "login" },
    { key: "probes" },
    { key: "destinations" },
    { key: "tasks" },
    { key: "metrics" },
    { key: "stats" },
  ] as const;

  const events = ["taskAssigned", "online", "result"] as const;

  const limits = ["createTask", "getTask"] as const;
</script>

<svelte:head>
  <title>XSpeed · {t("docs.title")}</title>
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
        <li
          class="rounded-card border border-surface-300 bg-surface-100/40 p-3"
        >
          <code class="font-mono text-xs text-primary">
            {t(`docs.endpoints.${endpoint.key}.title`)}
          </code>
          <p class="mt-1 text-surface-content/70">
            {t(`docs.endpoints.${endpoint.key}.description`)}
          </p>
        </li>
      {/each}
    </ul>
  </section>

  <section class="space-y-3">
    <h2
      class="text-sm font-semibold uppercase tracking-wide text-surface-content/60"
    >
      {t("docs.adminTitle")}
    </h2>
    <p class="text-sm text-surface-content/70">{t("docs.adminIntro")}</p>
    <ul class="space-y-2 text-sm">
      {#each adminEndpoints as endpoint (endpoint.key)}
        <li
          class="rounded-card border border-surface-300 bg-surface-100/40 p-3"
        >
          <code class="font-mono text-xs text-primary">
            {t(`docs.adminEndpoints.${endpoint.key}.title`)}
          </code>
          <p class="mt-1 text-surface-content/70">
            {t(`docs.adminEndpoints.${endpoint.key}.description`)}
          </p>
        </li>
      {/each}
    </ul>
    <p class="text-xs text-surface-content/60">{t("docs.adminAuth")}</p>
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
