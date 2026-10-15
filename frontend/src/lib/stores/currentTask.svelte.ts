import { writable } from 'svelte/store';

export const currentTaskId = writable<number | null>(null);