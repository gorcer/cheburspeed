export interface HistoryEntry {
  taskId: number;
  probeId: number;
  probeAlias: string;
  speedMbps: number | null;
  destAddress: string;
  createdAt: string;
}

class HistoryChecksStore {
  entries = $state<HistoryEntry[]>([]);

  private key(entry: HistoryEntry): string {
    return `${entry.taskId}:${entry.probeId}`;
  }

  private has(entry: HistoryEntry): boolean {
    const key = this.key(entry);
    return this.entries.some((existing) => this.key(existing) === key);
  }

  add(entry: HistoryEntry): void {
    if (this.has(entry)) return;
    this.entries = [...this.entries, entry];
  }

  clear(): void {
    this.entries = [];
  }
}

export const historyChecks = new HistoryChecksStore();
