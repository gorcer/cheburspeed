import { browser } from "$app/environment";
import { goto } from "$app/navigation";
import { page } from "$app/state";

import { auth } from "$lib/stores/auth.svelte";

export const ssr = false;
export const prerender = false;

const LOGIN_PATH = "/admin/login";

export function load(): Record<string, never> {
  if (!browser) return {};
  const pathname = page.url.pathname;
  if (pathname === LOGIN_PATH) {
    return {};
  }
  if (!auth.isAuthenticated) {
    void goto(LOGIN_PATH);
  }
  return {};
}
