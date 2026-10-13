import { browser } from '$app/environment';

const STORAGE_KEY = 'xspeed:admin-token';

class AuthStore {
  token = $state<string | null>(null);

  constructor() {
    if (browser) {
      this.token = window.localStorage.getItem(STORAGE_KEY);
    }
  }

  set(token: string) {
    this.token = token;
    if (browser) {
      window.localStorage.setItem(STORAGE_KEY, token);
    }
  }

  clear() {
    this.token = null;
    if (browser) {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }

  get isAuthenticated() {
    return this.token !== null;
  }
}

export const auth = new AuthStore();