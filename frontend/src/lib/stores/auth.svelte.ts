import { browser } from "$app/environment";

const STORAGE_KEY = "xspeed:admin-token";
const EMAIL_STORAGE_KEY = "xspeed:admin-email";

class AuthStore {
  token = $state<string | null>(null);
  email = $state<string | null>(null);

  constructor() {
    if (browser) {
      this.token = window.localStorage.getItem(STORAGE_KEY);
      this.email = window.localStorage.getItem(EMAIL_STORAGE_KEY);
    }
  }

  set(token: string, email?: string | null) {
    this.token = token;
    if (browser) {
      window.localStorage.setItem(STORAGE_KEY, token);
      if (email) {
        this.email = email;
        window.localStorage.setItem(EMAIL_STORAGE_KEY, email);
      }
    }
  }

  clear() {
    this.token = null;
    this.email = null;
    if (browser) {
      window.localStorage.removeItem(STORAGE_KEY);
      window.localStorage.removeItem(EMAIL_STORAGE_KEY);
    }
  }

  get isAuthenticated() {
    return this.token !== null;
  }
}

export const auth = new AuthStore();
