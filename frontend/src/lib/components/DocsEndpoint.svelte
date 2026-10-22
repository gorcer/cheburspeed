<script lang="ts">
  import { ChevronRight } from "@lucide/svelte";

  import { t } from "$lib/stores/locale.svelte";

  let {
    title,
    description,
    request,
    response,
    status,
  }: {
    title: string;
    description: string;
    request: string;
    response: string;
    status: string;
  } = $props();

  const blockClass =
    "overflow-x-auto rounded-card border border-surface-300/60 bg-surface-100/60 p-3 font-mono text-[11px] leading-relaxed whitespace-pre text-surface-content/85";
</script>

<details class="group rounded-card border border-surface-300 bg-surface-100/40">
  <summary
    class="flex cursor-pointer list-none items-start gap-2 p-3 [&::-webkit-details-marker]:hidden"
  >
    <ChevronRight
      size={14}
      aria-hidden="true"
      class="mt-0.5 shrink-0 text-surface-content/50 transition-transform group-open:rotate-90"
    />
    <span class="min-w-0 flex-1 space-y-1">
      <code class="block break-all font-mono text-xs text-primary">{title}</code
      >
      <span class="block text-sm text-surface-content/70">{description}</span>
    </span>
  </summary>

  <div class="space-y-3 border-t border-surface-300/60 p-3">
    <div class="space-y-1">
      <h3
        class="text-xs font-semibold uppercase tracking-wide text-surface-content/60"
      >
        {t("docs.examples.labels.request")}
      </h3>
      <pre class={blockClass}><code>{request}</code></pre>
    </div>

    <div class="space-y-1">
      <div class="flex flex-wrap items-center gap-2">
        <h3
          class="text-xs font-semibold uppercase tracking-wide text-surface-content/60"
        >
          {t("docs.examples.labels.response")}
        </h3>
        <span
          class="inline-flex rounded-pill bg-primary/15 px-2 py-0.5 font-mono text-[11px] text-primary"
        >
          {status}
        </span>
      </div>
      <pre class={blockClass}><code>{response}</code></pre>
    </div>
  </div>
</details>
