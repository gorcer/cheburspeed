import { browser } from "$app/environment";
import { goto } from "$app/navigation";
import type { LoadEvent } from "@sveltejs/kit";

import { auth } from "$lib/stores/auth.svelte";

export const ssr = false;
export const prerender = false;

const LOGIN_PATH = "/admin/login";

export function load({ url }: LoadEvent): Record<string, never> {
  if (!browser) return {};
  if (url.pathname === LOGIN_PATH) {
    return {};
  }
  if (!auth.isAuthenticated) {
    void goto(LOGIN_PATH);
  }
  return {};
}
