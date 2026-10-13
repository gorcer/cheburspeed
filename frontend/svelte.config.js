import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { mdsvex } from 'mdsvex';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  extensions: ['.svelte', '.svx'],
  preprocess: [vitePreprocess(), mdsvex()],
  kit: {
    adapter: adapter(),
    alias: {
      $lib: 'src/lib',
    },
  },
};

export default config;