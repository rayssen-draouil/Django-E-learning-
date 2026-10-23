(() => {
  const form = document.getElementById("quiz-answer-form");
  if (!form) return;

  const cards = [...form.querySelectorAll("[data-question-card]")];
  const links = [...document.querySelectorAll("[data-question-link]")];
  const answeredCount = document.getElementById("quiz-answered-count");
  const overallProgress = document.getElementById("quiz-overall-progress");
  let submitting = false;
  let interval = null;

  form.addEventListener("submit", (event) => {
    if (submitting) {
      event.preventDefault();
      return;
    }
    submitting = true;
    window.clearInterval(interval);
    const button = document.getElementById("quiz-submit-button");
    if (button) {
      button.disabled = true;
      button.textContent = "Envoi en cours…";
    }
  });

  const updateProgress = () => {
    let answered = 0;
    cards.forEach((card) => {
      const hasAnswer = [...card.querySelectorAll('input[type="radio"], input[type="checkbox"]')]
        .some((input) => input.checked);
      const link = links.find((item) => item.dataset.questionLink === card.dataset.questionCard);
      if (link) link.classList.toggle("is-answered", hasAnswer);
      if (hasAnswer) answered += 1;
    });
    if (answeredCount) answeredCount.textContent = String(answered);
    if (overallProgress) {
      const total = Number(form.dataset.questionCount) || cards.length;
      overallProgress.style.width = `${total ? (answered / total) * 100 : 0}%`;
    }
  };

  form.addEventListener("change", updateProgress);
  updateProgress();
  links.forEach((link) => link.addEventListener("click", (event) => {
    const target = document.getElementById(`quiz-question-${link.dataset.questionLink}`);
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }));

  const timer = document.getElementById("quiz-countdown");
  if (!timer || form.dataset.timerElapsed === "true") return;

  const timerCard = timer.closest(".quiz-timer-card");
  const timerProgress = document.getElementById("quiz-timer-progress");
  const timerMessage = document.getElementById("quiz-timer-message");
  const submitButton = document.getElementById("quiz-submit-button");
  const total = Number(timer.dataset.duration) || 0;
  let remaining = Math.max(0, Number(timer.dataset.remaining) || 0);

  const renderTimer = () => {
    const minutes = Math.floor(remaining / 60);
    const seconds = remaining % 60;
    timer.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    timer.dateTime = `PT${remaining}S`;
    if (timerProgress) timerProgress.style.width = `${total ? (remaining / total) * 100 : 0}%`;
    if (timerCard) {
      timerCard.classList.toggle("is-warning", remaining <= 60 && remaining > 20);
      timerCard.classList.toggle("is-critical", remaining <= 20);
    }
  };

  renderTimer();
  interval = window.setInterval(() => {
    remaining = Math.max(0, remaining - 1);
    renderTimer();
    if (remaining > 0) return;

    window.clearInterval(interval);
    if (timerMessage) timerMessage.textContent = "Temps écoulé : envoi de vos réponses…";
    if (submitButton && !submitting) form.requestSubmit(submitButton);
  }, 1000);

})();
