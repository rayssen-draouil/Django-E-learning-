(() => {
  const grid = document.getElementById("quiz-list-grid");
  if (!grid) return;

  const search = document.getElementById("quiz-list-search");
  const statusFilter = document.getElementById("quiz-list-status");
  const sortSelect = document.getElementById("quiz-list-sort");
  const liveCount = document.getElementById("quiz-list-live-count");
  const noResults = document.getElementById("quiz-list-no-results");
  const resetButton = document.getElementById("quiz-list-reset");
  const cards = [...grid.querySelectorAll("[data-quiz-card]")];

  const update = () => {
    const term = search.value.trim().toLocaleLowerCase();
    const status = statusFilter.value;
    let visible = 0;

    cards.forEach((card) => {
      const matchesText = !term || card.textContent.toLocaleLowerCase().includes(term);
      const matchesStatus = status === "all" || card.dataset.status === status;
      card.hidden = !(matchesText && matchesStatus);
      if (!card.hidden) visible += 1;
    });

    liveCount.textContent = `${visible} result${visible === 1 ? "" : "s"}`;
    noResults.hidden = cards.length === 0 || visible > 0;
  };

  const sortCards = () => {
    const mode = sortSelect.value;
    const sorted = [...cards].sort((first, second) => {
      if (mode === "title") return first.dataset.title.localeCompare(second.dataset.title, "en");
      if (mode === "questions") {
        return Number(second.dataset.questionCount) - Number(first.dataset.questionCount)
          || first.dataset.title.localeCompare(second.dataset.title, "en");
      }
      return Number(second.dataset.created) - Number(first.dataset.created);
    });
    sorted.forEach((card) => grid.append(card));
  };

  search.addEventListener("input", update);
  statusFilter.addEventListener("change", update);
  sortSelect.addEventListener("change", sortCards);
  resetButton?.addEventListener("click", () => {
    search.value = "";
    statusFilter.value = "all";
    update();
    search.focus();
  });

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      search.focus();
    }
    if (event.key === "Escape" && document.activeElement === search) {
      search.value = "";
      update();
      search.blur();
    }
  });

  update();
})();
