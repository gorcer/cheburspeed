import { browser } from '$app/environment';

import en from '$lib/locale/en.json';
import ru from '$lib/locale/ru.json';

export type LocaleCode = 'ru' | 'en';

type Dictionary = typeof ru;
type Path<T> = T extends object
  ? {
      [K in keyof T & string]: T[K] extends object ? `${K}.${Path<T[K]>}` : K;
    }[keyof T & string]
  : never;

const dictionaries: Record<LocaleCode, Dictionary> = { ru, en };

const STORAGE_KEY = 'xspeed:locale';

function readStored(): LocaleCode | null {
  if (!browser) return null;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === 'ru' || raw === 'en') return raw;
  return null;
}

function writeStored(code: LocaleCode) {
  if (!browser) return;
  window.localStorage.setItem(STORAGE_KEY, code);
  document.documentElement.lang = code;
}

function resolve(code: LocaleCode, path: string): string {
  const segments = path.split('.');
  let cursor: unknown = dictionaries[code];
  for (const segment of segments) {
    if (cursor && typeof cursor === 'object' && segment in (cursor as Record<string, unknown>)) {
      cursor = (cursor as Record<string, unknown>)[segment];
    } else {
      return path;
    }
  }
  return typeof cursor === 'string' ? cursor : path;
}

function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, name: string) => {
    const value = params[name];
    return value === undefined ? `{{${name}}}` : String(value);
  });
}

class LocaleStore {
  code = $state<LocaleCode>(readStored() ?? 'ru');

  constructor() {
    if (browser) {
      document.documentElement.lang = this.code;
    }
  }

  set(code: LocaleCode) {
    this.code = code;
    writeStored(code);
  }

  toggle() {
    this.set(this.code === 'ru' ? 'en' : 'ru');
  }

  t(path: Path<Dictionary>, params?: Record<string, string | number>): string {
    return interpolate(resolve(this.code, path), params);
  }
}

export const locale = new LocaleStore();
export const t = (path: Path<Dictionary>, params?: Record<string, string | number>) =>
  locale.t(path, params);