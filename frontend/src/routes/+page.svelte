<script lang="ts">
  import HistoryTable from '$lib/components/HistoryTable.svelte';
  import Map from '$lib/components/Map.svelte';
  import ProbeForm from '$lib/components/ProbeForm.svelte';
  import ResultsTable from '$lib/components/ResultsTable.svelte';
</script>

<svelte:head>
  <title>XSpeed — карта скоростей VLESS</title>
</svelte:head>

<div class="space-y-6">
  <ProbeForm />
  <Map />
  <ResultsTable />
  <HistoryTable />
</div>