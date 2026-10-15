class CurrentTaskStore {
  id = $state<number | null>(null);

  set(id: number | null) {
    this.id = id;
  }

  clear() {
    this.id = null;
  }
}

export const currentTask = new CurrentTaskStore();