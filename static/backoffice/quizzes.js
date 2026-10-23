(() => {
  const source = document.getElementById("id_questions_json");
  if (!source) return;
  let questions = [];
  try { questions = JSON.parse(source.value || "[]"); } catch { return; }
  if (!Array.isArray(questions)) return;

  const builder = document.createElement("div");
  builder.className = "quiz-builder";
  source.insertAdjacentElement("beforebegin", builder);
  source.classList.add("quiz-json-source");
  source.required = false;

  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const getQuestion = (card) => {
    const type = card.querySelector("[data-type]").value;
    const prompt = card.querySelector("[data-prompt]").value;
    const points = Number(card.querySelector("[data-points]").value || 1);
    const options = [...card.querySelectorAll("[data-option]")].map((row) => ({
      _id: row.dataset.id || undefined,
      text: row.querySelector("input[type=text]").value,
      correct: row.querySelector("input[type=checkbox], input[type=radio]").checked,
    }));
    return { ...(card.dataset.id ? { _id: card.dataset.id } : {}), type, prompt, points, options };
  };

  const renderQuestion = (question = {}) => {
    const card = element("section", "quiz-builder-card");
    if (question._id) card.dataset.id = question._id;
    const top = element("div", "quiz-builder-top");
    const typeLabel = element("label", "", "Type de question");
    const type = element("select");
    type.dataset.type = "";
    [["single_choice", "Choix unique"], ["multiple_choice", "Choix multiples"], ["true_false", "Vrai / Faux"]].forEach(([value, label]) => {
      const option = element("option", "", label);
      option.value = value;
      option.selected = question.type === value;
      type.append(option);
    });
    if (!question.type) type.value = "single_choice";
    typeLabel.append(type);
    const pointsLabel = element("label", "", "Points");
    const points = element("input");
    points.type = "number";
    points.min = "0.1";
    points.step = "0.1";
    points.value = question.points || 1;
    points.dataset.points = "";
    pointsLabel.append(points);
    const remove = element("button", "quiz-builder-remove", "Supprimer la question");
    remove.type = "button";
    remove.addEventListener("click", () => { card.remove(); sync(); });
    top.append(typeLabel, pointsLabel, remove);

    const promptLabel = element("label", "", "Énoncé");
    const prompt = element("textarea");
    prompt.rows = 2;
    prompt.value = question.prompt || "";
    prompt.dataset.prompt = "";
    promptLabel.append(prompt);
    const optionsWrap = element("div", "quiz-builder-options");
    const answerGroup = crypto.randomUUID();
    const addOption = element("button", "outline-button", "Ajouter une option");
    addOption.type = "button";
    const options = Array.isArray(question.options) && question.options.length
      ? question.options
      : [{ text: "Option A" }, { text: "Option B" }];
    const correctIds = question.correct_option_ids || [];
    const rows = [];
    const addOptionRow = (option = {}, index = rows.length) => {
      const row = element("div", "quiz-builder-option");
      if (option._id) row.dataset.id = option._id;
      const answer = element("input");
      answer.type = type.value === "multiple_choice" ? "checkbox" : "radio";
      answer.name = `correct-${answerGroup}`;
      answer.checked = correctIds.includes(index) || correctIds.includes(String(index)) || correctIds.includes(option._id);
      const text = element("input");
      text.type = "text";
      text.value = option.text || "";
      text.placeholder = "Texte de l’option";
      const label = element("label", "quiz-builder-answer");
      label.append(answer, element("span", "", "Bonne réponse"));
      const deleteOption = element("button", "quiz-builder-remove", "Retirer");
      deleteOption.type = "button";
      deleteOption.addEventListener("click", () => {
        if (optionsWrap.querySelectorAll("[data-option]").length > 2) {
          row.remove();
          sync();
        }
      });
      row.dataset.option = "";
      row.append(text, label, deleteOption);
      optionsWrap.append(row);
      rows.push(row);
    };
    options.forEach(addOptionRow);
    addOption.addEventListener("click", () => {
      addOptionRow({ text: "" });
      sync();
    });
    type.addEventListener("change", () => {
      optionsWrap.querySelectorAll("input[type=checkbox], input[type=radio]").forEach((input) => {
        input.type = type.value === "multiple_choice" ? "checkbox" : "radio";
        input.name = `correct-${answerGroup}`;
      });
      sync();
    });
    card.append(top, promptLabel, element("p", "quiz-builder-hint", "Sélectionnez la ou les bonnes réponses."), optionsWrap, addOption);
    card.addEventListener("input", sync);
    builder.append(card);
  };

  const sync = () => {
    const questions = [...builder.querySelectorAll(".quiz-builder-card")].map((card) => {
      const question = getQuestion(card);
      question.options = question.options.map(({ correct, ...option }) => option);
      question.correct_option_ids = [...card.querySelectorAll("[data-option]")]
        .map((row, index) => row.querySelector("input[type=checkbox], input[type=radio]").checked ? index : null)
        .filter((index) => index !== null);
      return question;
    });
    source.value = JSON.stringify(questions, null, 2);
  };

  questions.forEach(renderQuestion);
  const addQuestion = element("button", "primary-button", "Ajouter une question");
  addQuestion.type = "button";
  addQuestion.addEventListener("click", () => {
    renderQuestion({ type: "single_choice", prompt: "", points: 1,
      options: [{ text: "Option A" }, { text: "Option B" }], correct_option_ids: [0] });
    sync();
  });
  builder.append(addQuestion);
  if (questions.length) sync();

  const form = source.form;
  const addError = (container, message) => {
    let error = container.querySelector(":scope > .quiz-client-error");
    if (!error) {
      error = element("p", "quiz-client-error");
      container.append(error);
    }
    const messages = error.textContent ? error.textContent.split("\n") : [];
    if (!messages.includes(message)) messages.push(message);
    error.textContent = messages.join("\n");
  };
  form.addEventListener("submit", (event) => {
    if (event.submitter?.name === "import_json") return;
    form.querySelectorAll(".quiz-client-error").forEach((error) => error.remove());
    sync();
    const fieldValue = (name) => form.querySelector(`[name="${name}"]`)?.value.trim() || "";
    const title = fieldValue("title");
    const description = fieldValue("description");
    if (!title) addError(form.querySelector('[name="title"]').closest(".quiz-field"), "Saisissez un titre.");
    if (!description) addError(form.querySelector('[name="description"]').closest(".quiz-field"), "Saisissez une description.");

    const duration = fieldValue("duration_seconds");
    if (duration && (!Number.isInteger(Number(duration)) || Number(duration) < 1)) {
      addError(form.querySelector('[name="duration_seconds"]').closest(".quiz-field"), "La durée doit être un entier supérieur à zéro.");
    }
    const passingScore = fieldValue("passing_score");
    if (passingScore && (!Number.isInteger(Number(passingScore)) || Number(passingScore) < 0 || Number(passingScore) > 100)) {
      addError(form.querySelector('[name="passing_score"]').closest(".quiz-field"), "Le score minimal doit être un entier entre 0 et 100.");
    }

    let data;
    try { data = JSON.parse(source.value); } catch { data = null; }
    const questionsField = source.closest(".quiz-field");
    if (!Array.isArray(data) || !data.length) {
      addError(questionsField, "Ajoutez au moins une question valide.");
    } else if (data.length > 100) {
      addError(questionsField, "Un quiz ne peut pas contenir plus de 100 questions.");
    } else {
      data.forEach((question, index) => {
        const card = builder.querySelectorAll(".quiz-builder-card")[index];
        if (!card || !question || typeof question !== "object") {
          addError(questionsField, `La question ${index + 1} est invalide.`);
          return;
        }
        const selected = Array.isArray(question.correct_option_ids) ? question.correct_option_ids : [];
        if (!question.prompt?.trim()) addError(card, `La question ${index + 1} doit avoir un énoncé.`);
        if (!Number.isFinite(Number(question.points)) || Number(question.points) <= 0) addError(card, `Les points de la question ${index + 1} doivent être supérieurs à zéro.`);
        if (!Array.isArray(question.options) || question.options.length < 2 || question.options.length > 20 || question.options.some((option) => !option || typeof option.text !== "string" || !option.text.trim())) {
          addError(card, `La question ${index + 1} doit contenir au moins deux options avec un texte.`);
        }
        if (!selected.length) addError(card, `Sélectionnez une bonne réponse pour la question ${index + 1}.`);
        if (["single_choice", "true_false"].includes(question.type) && selected.length !== 1) {
          addError(card, `La question ${index + 1} exige exactement une bonne réponse.`);
        }
        if (question.type === "true_false" && question.options?.length === 2) {
          const labels = question.options.map((option) => option.text.trim().toLowerCase()).sort().join("|");
          if (!["faux|vrai", "false|true"].includes(labels)) addError(card, "Une question vrai/faux doit proposer Vrai et Faux.");
        }
      });
    }
    if (form.querySelector(".quiz-client-error")) event.preventDefault();
  });
})();
