<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/state";

  import { auth } from "$lib/stores/auth.svelte";
  import { t } from "$lib/stores/locale.svelte";

  let { children } = $props();

  const tabs = $derived([
    { href: "/admin", label: t("admin.tabs.dashboard") },
    { href: "/admin/probes", label: t("admin.tabs.probes") },
    { href: "/admin/tasks", label: t("admin.tabs.tasks") },
  ]);

  const isActive = (href: string) => {
    if (href === "/admin") return page.url.pathname === "/admin";
    return (
      page.url.pathname === href || page.url.pathname.startsWith(`${href}/`)
    );
  };

  function signOut() {
    auth.clear();
    void goto("/admin/login");
  }
</script>

<section class="space-y-5">
  <header
    class="flex flex-wrap items-center justify-between gap-3 rounded-card border border-surface-300 bg-surface-200/60 px-5 py-4"
  >
    <div>
      <h1 class="text-base font-semibold text-surface-content">
        {t("admin.title")}
      </h1>
      {#if auth.email}
        <p class="text-xs text-surface-content/60">{auth.email}</p>
      {/if}
    </div>
    <button
      type="button"
      onclick={signOut}
      class="inline-flex items-center rounded-pill border border-surface-300 px-3 py-1.5 text-xs text-surface-content/70 transition hover:bg-surface-300/30"
    >
      {t("admin.logout")}
    </button>
  </header>

  <nav
    aria-label={t("admin.tabs.dashboard")}
    class="flex flex-wrap gap-1 rounded-card border border-surface-300 bg-surface-200/40 p-1 text-sm"
  >
    {#each tabs as tab (tab.href)}
      <a
        href={tab.href}
        class={`rounded-pill px-3 py-1.5 transition ${
          isActive(tab.href)
            ? "bg-primary/15 text-primary"
            : "text-surface-content/70 hover:bg-surface-300/40 hover:text-surface-content"
        }`}
      >
        {tab.label}
      </a>
    {/each}
  </nav>

  {@render children()}
</section>
