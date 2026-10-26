(() => {
  const modal = document.getElementById("quiz-ai-modal");
  if (!modal) return;

  const generationForm = document.getElementById("quiz-ai-generation-form");
  const previewStage = document.getElementById("quiz-ai-preview-stage");
  const questionList = document.getElementById("quiz-ai-preview-questions");
  const errorBox = document.getElementById("quiz-ai-error");
  const status = document.getElementById("quiz-ai-status");
  const generateButton = document.getElementById("quiz-ai-generate");
  const saveButton = document.getElementById("quiz-ai-save");
  let busy = false;
  let activeRequest = null;
  let lastTrigger = null;

  const node = (tag, className, text) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  };

  const showError = (message) => {
    errorBox.textContent = message;
    errorBox.hidden = !message;
  };

  const setBusy = (button, loading, label) => {
    busy = loading;
    button.disabled = loading;
    if (loading) {
      button.replaceChildren(node("span", "quiz-ai-spinner"), node("span", "", label));
      button.classList.add("quiz-ai-loading");
    } else {
      button.classList.remove("quiz-ai-loading");
      button.textContent = label;
    }
  };

  const csrfToken = () => generationForm.querySelector('[name="csrfmiddlewaretoken"]').value;
  const setStage = (step) => {
    document.getElementById("quiz-ai-step-label").textContent = `Step ${step} of 2`;
    modal.querySelectorAll("[data-quiz-ai-step]").forEach((item) => {
      const itemStep = Number(item.dataset.quizAiStep);
      item.classList.toggle("is-active", itemStep === step);
      item.classList.toggle("is-complete", itemStep < step);
    });
  };
  const updateQuestionMeta = () => {
    const cards = [...questionList.querySelectorAll("[data-question-card]")];
    cards.forEach((card, index) => {
      const title = card.querySelector(".quiz-ai-preview-question-header span");
      if (title) title.textContent = `Question ${String(index + 1).padStart(2, "0")}`;
      card.querySelectorAll('.quiz-ai-preview-option input[type="radio"], .quiz-ai-preview-option input[type="checkbox"]')
        .forEach((answer) => { answer.name = `ai-question-${index}-correct`; });
    });
    const count = document.getElementById("quiz-ai-question-count");
    count.textContent = `${cards.length} question${cards.length === 1 ? "" : "s"}`;
  };
  const postJSON = async (url, payload) => {
    activeRequest = new AbortController();
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      signal: activeRequest.signal,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(payload),
    });
    let data;
    try { data = await response.json(); } catch { throw new Error("The server returned an unexpected response."); }
    if (!response.ok) throw new Error(data.error || "The request could not be completed.");
    return data;
  };

  const markAsOpen = (open) => {
    modal.hidden = !open;
    document.body.classList.toggle("quiz-ai-modal-open", open);
    if (open) {
      const focusTarget = document.getElementById("quiz-ai-topic");
      window.setTimeout(() => focusTarget.focus(), 0);
    } else if (lastTrigger) {
      lastTrigger.focus();
    }
  };

  const resetModal = () => {
    activeRequest?.abort();
    activeRequest = null;
    busy = false;
    generationForm.reset();
    generationForm.hidden = false;
    previewStage.hidden = true;
    questionList.replaceChildren();
    document.getElementById("quiz-ai-preview-title").value = "";
    document.getElementById("quiz-ai-preview-description").value = "";
    const previewDuration = document.getElementById("quiz-ai-preview-duration");
    if (previewDuration) previewDuration.value = "";
    errorBox.hidden = true;
    errorBox.textContent = "";
    status.textContent = "";
    generateButton.disabled = false;
    generateButton.classList.remove("quiz-ai-loading");
    generateButton.textContent = "Generate with AI";
    saveButton.disabled = false;
    saveButton.classList.remove("quiz-ai-loading");
    saveButton.textContent = "Create quiz as draft";
    setStage(1);
    updateQuestionMeta();
  };

  document.querySelectorAll("[data-quiz-ai-open]").forEach((button) => {
    button.addEventListener("click", () => {
      lastTrigger = button;
      showError("");
      markAsOpen(true);
    });
  });
  modal.querySelectorAll("[data-quiz-ai-close]").forEach((button) => {
    button.addEventListener("click", () => {
      resetModal();
      markAsOpen(false);
    });
  });
  document.addEventListener("keydown", (event) => {
    if (modal.hidden) return;
    if (event.key === "Escape" && !busy) {
      resetModal();
      markAsOpen(false);
    }
    if (event.key === "Tab") {
      const focusable = [...modal.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]')]
        .filter((item) => !item.closest("[hidden]"));
      if (!focusable.length) return;
      if (event.shiftKey && document.activeElement === focusable[0]) {
        event.preventDefault();
        focusable.at(-1).focus();
      } else if (!event.shiftKey && document.activeElement === focusable.at(-1)) {
        event.preventDefault();
        focusable[0].focus();
      }
    }
  });

  const renderQuestion = (question = {}, index) => {
    const card = node("article", "quiz-ai-preview-question");
    card.dataset.questionCard = "";
    const header = node("div", "quiz-ai-preview-question-header");
    header.append(node("span", "", `Question ${index + 1}`));
    const removeQuestion = node("button", "quiz-ai-remove-question", "Delete");
    removeQuestion.type = "button";
    removeQuestion.addEventListener("click", () => {
      card.remove();
      updateQuestionMeta();
    });
    header.append(removeQuestion);

    const grid = node("div", "quiz-ai-preview-question-grid");
    const typeLabel = node("label", "", "Question type");
    const type = document.createElement("select");
    type.dataset.questionType = "";
    [["single_choice", "Single choice"], ["multiple_choice", "Multiple choice"], ["true_false", "True / False"]].forEach(([value, label]) => {
      const option = node("option", "", label);
      option.value = value;
      option.selected = question.type === value;
      type.append(option);
    });
    typeLabel.append(type);
    const pointsLabel = node("label", "", "Points");
    const points = document.createElement("input");
    points.type = "number";
    points.min = "0.1";
    points.max = "5";
    points.step = "0.1";
    points.value = question.points ?? 1;
    points.dataset.questionPoints = "";
    pointsLabel.append(points);
    grid.append(typeLabel, pointsLabel);

    const promptLabel = node("label", "", "Question prompt");
    const prompt = document.createElement("textarea");
    prompt.rows = 2;
    prompt.maxLength = 2000;
    prompt.value = question.prompt || "";
    prompt.required = true;
    prompt.dataset.questionPrompt = "";
    promptLabel.append(prompt);

    const options = node("div", "quiz-ai-preview-options");
    const correctIndices = Array.isArray(question.correct_option_ids) ? question.correct_option_ids : [];
    const addOption = (option = {}, optionIndex = options.childElementCount) => {
      const row = node("div", "quiz-ai-preview-option");
      const answer = document.createElement("input");
      answer.type = type.value === "multiple_choice" ? "checkbox" : "radio";
      answer.name = `ai-question-${index}-correct`;
      answer.checked = correctIndices.includes(optionIndex);
      answer.setAttribute("aria-label", `Correct answer for question ${index + 1}, option ${optionIndex + 1}`);
      const text = document.createElement("input");
      text.type = "text";
      text.className = "quiz-ai-option-text";
      text.maxLength = 500;
      text.required = true;
      text.value = typeof option === "string" ? option : (option.text || "");
      text.placeholder = `Option ${String.fromCharCode(65 + optionIndex)}`;
      const removeOption = node("button", "quiz-ai-remove-option", "Remove");
      removeOption.type = "button";
      removeOption.setAttribute("aria-label", `Remove option ${optionIndex + 1}`);
      removeOption.addEventListener("click", () => {
        if (options.children.length <= 2) {
          showError("Each question must have at least two options.");
          return;
        }
        row.remove();
        showError("");
      });
      row.append(answer, text, removeOption);
      options.append(row);
    };

    type.addEventListener("change", () => {
      options.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach((input) => {
        input.type = type.value === "multiple_choice" ? "checkbox" : "radio";
        input.name = `ai-question-${index}-correct`;
      });
    });
    card.append(header, grid, promptLabel, options);
    (Array.isArray(question.options) ? question.options : []).forEach(addOption);
    const addOptionButton = node("button", "quiz-ai-add-option", "+ Add an option");
    addOptionButton.type = "button";
    addOptionButton.addEventListener("click", () => {
      if (options.children.length >= 20) {
        showError("A question cannot have more than 20 options.");
        return;
      }
      addOption({ text: "" });
      showError("");
    });
    card.append(addOptionButton);
    return card;
  };

  const displayPreview = (quiz) => {
    document.getElementById("quiz-ai-preview-title").value = quiz.title;
    document.getElementById("quiz-ai-preview-description").value = quiz.description;
    const previewDuration = document.getElementById("quiz-ai-preview-duration");
    if (previewDuration) {
      if (quiz.duration_minutes !== undefined && quiz.duration_minutes !== null) {
        previewDuration.value = quiz.duration_minutes;
      } else {
        const setupDuration = document.getElementById("quiz-ai-duration");
        previewDuration.value = setupDuration ? setupDuration.value : "15";
      }
    }
    questionList.replaceChildren(...quiz.questions.map((question, index) => renderQuestion(question, index)));
    updateQuestionMeta();
    setStage(2);
    generationForm.hidden = true;
    previewStage.hidden = false;
    status.textContent = "Review the quiz and its correct answers. It will only be saved after you confirm.";
    showError("");
    document.getElementById("quiz-ai-preview-title").focus();
  };

  generationForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    showError("");
    const required = ["quiz-ai-topic", "quiz-ai-description", "quiz-ai-objectives"];
    const empty = required.map((id) => document.getElementById(id)).find((field) => !field.value.trim());
    if (empty) {
      showError("Topic, description, and learning objectives are required.");
      empty.focus();
      return;
    }
    const formData = new FormData(generationForm);
    const payload = Object.fromEntries(formData.entries());
    delete payload.csrfmiddlewaretoken;
    payload.question_count = Number(payload.question_count);
    if (payload.duration_minutes !== undefined && payload.duration_minutes !== "") {
      payload.duration_minutes = Number(payload.duration_minutes);
    }
    setBusy(generateButton, true, "Generating…");
    status.textContent = "Gemini is preparing the questions. This may take a few seconds.";
    try {
      const result = await postJSON(modal.dataset.generateUrl, payload);
      displayPreview(result.quiz);
    } catch (error) {
      if (error.name !== "AbortError") showError(error.message || "Generation failed. Please try again.");
      status.textContent = "";
    } finally {
      activeRequest = null;
      setBusy(generateButton, false, "Generate with AI");
    }
  });

  document.getElementById("quiz-ai-back").addEventListener("click", () => {
    previewStage.hidden = true;
    generationForm.hidden = false;
    setStage(1);
    status.textContent = "Change the settings and generate a new quiz.";
    showError("");
    document.getElementById("quiz-ai-topic").focus();
  });

  document.getElementById("quiz-ai-add-question").addEventListener("click", () => {
    if (questionList.children.length >= 100) {
      showError("A quiz cannot have more than 100 questions.");
      return;
    }
    questionList.append(renderQuestion({
      type: "single_choice", prompt: "", points: 1,
      options: [{ text: "Option A" }, { text: "Option B" }], correct_option_ids: [0],
    }, questionList.children.length));
    updateQuestionMeta();
    showError("");
  });

  const readPreview = () => {
    const title = document.getElementById("quiz-ai-preview-title").value.trim();
    const description = document.getElementById("quiz-ai-preview-description").value.trim();
    const previewDuration = document.getElementById("quiz-ai-preview-duration");
    let duration_seconds = null;
    let duration_minutes = 0;
    if (previewDuration && previewDuration.value.trim() !== "") {
      const mins = Number(previewDuration.value.trim());
      if (Number.isFinite(mins) && mins > 0) {
        duration_minutes = Math.round(mins);
        duration_seconds = duration_minutes * 60;
      }
    }
    const questions = [...questionList.querySelectorAll("[data-question-card]")].map((card) => ({
      type: card.querySelector("[data-question-type]").value,
      prompt: card.querySelector("[data-question-prompt]").value.trim(),
      points: Number(card.querySelector("[data-question-points]").value),
      options: [...card.querySelectorAll(".quiz-ai-preview-option")].map((row) => ({
        text: row.querySelector(".quiz-ai-option-text").value.trim(),
      })),
      correct_option_ids: [...card.querySelectorAll(".quiz-ai-preview-option")]
        .map((row, index) => row.querySelector('input[type="radio"], input[type="checkbox"]').checked ? index : null)
        .filter((index) => index !== null),
    }));
    return { title, description, duration_seconds, duration_minutes, questions };
  };

  saveButton.addEventListener("click", async () => {
    if (busy) return;
    showError("");
    const payload = readPreview();
    if (!payload.title || !payload.description) {
      showError("Title and description are required.");
      return;
    }
    const previewDuration = document.getElementById("quiz-ai-preview-duration");
    if (previewDuration && previewDuration.value.trim() !== "") {
      const mins = Number(previewDuration.value.trim());
      if (!Number.isFinite(mins) || mins < 0 || mins > 300) {
        showError("Quiz duration must be between 0 and 300 minutes.");
        previewDuration.focus();
        return;
      }
    }
    if (!payload.questions.length || payload.questions.length > 100) {
      showError("The quiz must contain between 1 and 100 questions.");
      return;
    }
    for (let index = 0; index < payload.questions.length; index += 1) {
      const question = payload.questions[index];
      if (!question.prompt || !Number.isFinite(question.points) || question.points <= 0 || question.points > 1000) {
        showError(`Check the prompt and points for question ${index + 1}.`);
        return;
      }
      if (question.options.length < 2 || question.options.length > 20 || question.options.some((option) => !option.text)) {
        showError(`Question ${index + 1} must have 2 to 20 options, each with text.`);
        return;
      }
      if (!question.correct_option_ids.length || (["single_choice", "true_false"].includes(question.type) && question.correct_option_ids.length !== 1)) {
        showError(`Check the correct answers for question ${index + 1}.`);
        return;
      }
      if (question.type === "true_false") {
        const labels = question.options.map((option) => option.text.toLowerCase()).sort().join("|");
        if (!["faux|vrai", "false|true"].includes(labels)) {
          showError(`True/false question ${index + 1} must provide True and False options.`);
          return;
        }
      }
    }

    setBusy(saveButton, true, "Saving…");
    status.textContent = "Validating and saving the draft…";
    try {
      const result = await postJSON(modal.dataset.createUrl, payload);
      window.location.assign(result.redirect_url);
    } catch (error) {
      if (error.name !== "AbortError") showError(error.message || "The draft could not be saved.");
      status.textContent = "";
    } finally {
      activeRequest = null;
      setBusy(saveButton, false, "Create quiz as draft");
    }
  });
})();
