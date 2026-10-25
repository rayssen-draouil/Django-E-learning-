(function () {
    "use strict";

    const statusEl = document.getElementById("ai-status");
    const errorEl = document.getElementById("ai-error");
    const retryBtn = document.getElementById("ai-retry");
    const csrfToken = () => {
        const input = document.querySelector("[name=csrfmiddlewaretoken]");
        return input ? input.value : "";
    };
    const CATEGORY_LABELS = {
        programming: "Programmation",
        web: "Développement web",
        data: "Données & IA",
        design: "Design",
        business: "Business",
        other: "Autre",
    };
    const LEVEL_LABELS = {
        beginner: "Débutant",
        intermediate: "Intermédiaire",
        advanced: "Avancé",
    };

    let lastAction = null;

    const setStatus = (message) => {
        if (statusEl) {
            statusEl.textContent = message || "";
        }
    };

    const setError = (message) => {
        if (!errorEl) {
            return;
        }
        if (message) {
            errorEl.hidden = false;
            errorEl.textContent = message;
            if (retryBtn) {
                retryBtn.hidden = false;
            }
        } else {
            errorEl.hidden = true;
            errorEl.textContent = "";
            if (retryBtn) {
                retryBtn.hidden = true;
            }
        }
    };

    const fieldValue = (id) => {
        const el = document.getElementById(id);
        return el ? el.value.trim() : "";
    };

    const setBusy = (button, busy) => {
        button.disabled = busy;
        button.setAttribute("aria-busy", busy ? "true" : "false");
        if (busy) {
            button.dataset.originalLabel = button.textContent.trim();
            button.textContent = "Génération en cours...";
        } else if (button.dataset.originalLabel) {
            button.textContent = button.dataset.originalLabel;
        }
    };

    const readJson = async (response, fallbackMessage) => {
        try {
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || fallbackMessage);
            }
            return data;
        } catch (error) {
            if (error instanceof SyntaxError) {
                throw new Error(fallbackMessage);
            }
            throw error;
        }
    };

    const poll = async (statusUrlTemplate, taskId, csrf) => {
        const url = statusUrlTemplate.replace("__TASK__", encodeURIComponent(taskId));
        for (let attempt = 0; attempt < 30; attempt += 1) {
            await new Promise((resolve) => setTimeout(resolve, 1000));
            const response = await fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: { "X-CSRFToken": csrf, Accept: "application/json" },
                body: new URLSearchParams({}),
            });
            const data = await readJson(
                response,
                "Impossible de récupérer le statut de la génération."
            );
            if (data.status === "done") {
                return data;
            }
            if (data.status === "error") {
                throw new Error(data.error || "La génération IA a échoué.");
            }
        }
        throw new Error("La génération IA prend trop de temps. Réessayez.");
    };

    const applyObjectives = (objectives) => {
        const field = document.getElementById("id_objectives");
        if (!field || !Array.isArray(objectives)) {
            return;
        }
        field.value = objectives.join("\n");
        setStatus("Cinq objectifs ont été générés. Vérifiez-les avant d'enregistrer.");
    };

    const showImproveSuggestion = (text) => {
        const box = document.getElementById("ai-improve-suggestion");
        const textEl = document.getElementById("ai-improve-text");
        if (!box || !textEl) {
            return;
        }
        textEl.textContent = text;
        box.hidden = false;
        setStatus("Suggestion de description prête. Acceptez-la ou rejetez-la.");
    };

    const showMetaSuggestion = (meta) => {
        const box = document.getElementById("ai-meta-suggestion");
        const list = document.getElementById("ai-meta-list");
        if (!box || !list) {
            return;
        }
        list.replaceChildren();
        const rows = [
            ["Catégorie", CATEGORY_LABELS[meta.category] || meta.category || ""],
            ["Niveau", LEVEL_LABELS[meta.level] || meta.level || ""],
            ["Tags", Array.isArray(meta.tags) ? meta.tags.join(", ") : ""],
        ];
        rows.forEach(([label, value]) => {
            const dt = document.createElement("dt");
            dt.textContent = label;
            const dd = document.createElement("dd");
            dd.textContent = value;
            list.append(dt, dd);
        });
        box.dataset.category = meta.category || "";
        box.dataset.level = meta.level || "";
        box.dataset.tags = Array.isArray(meta.tags) ? meta.tags.join(", ") : "";
        box.hidden = false;
        setStatus("Suggestion de catégorie, niveau et tags prête. Appliquez-la ou rejetez-la.");
    };

    const runAction = async (button) => {
        const action = button.dataset.aiAction;
        const actionUrl = button.dataset.aiUrl;
        const statusUrl = button.dataset.aiStatusUrl;
        const title = fieldValue("id_title");
        const description = fieldValue("id_description");
        const level = fieldValue("id_level");
        const csrf = csrfToken();

        if (!actionUrl || !statusUrl || !action) {
            setError("La configuration du bouton IA est incomplète. Rechargez la page.");
            return;
        }
        if (!csrf) {
            setError("Le jeton de sécurité est introuvable. Rechargez la page avant de réessayer.");
            return;
        }

        if (title.length < 3) {
            setError("Saisissez d'abord un titre d'au moins 3 caractères.");
            return;
        }
        if (action === "improve_description" && description.length < 10) {
            setError("La description doit comporter au moins 10 caractères pour être améliorée.");
            return;
        }

        lastAction = button;
        setError("");
        setBusy(button, true);
        setStatus("Génération en cours...");
        try {
            const response = await fetch(actionUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: { "X-CSRFToken": csrf, Accept: "application/json" },
                body: new URLSearchParams({ title, description, level, action }),
            });
            const data = await readJson(response, "Le service IA est indisponible.");
            if (!data.task_id) {
                throw new Error("La génération IA n'a pas retourné d'identifiant de tâche.");
            }
            const result = await poll(statusUrl, data.task_id, csrf);
            if (action === "objectives") {
                applyObjectives(result.objectives || []);
            } else if (action === "improve_description") {
                showImproveSuggestion(result.improved_description || "");
            } else if (action === "suggest_meta") {
                showMetaSuggestion(result);
            }
        } catch (error) {
            setStatus("");
            setError(error.message || "La génération IA a échoué.");
        } finally {
            setBusy(button, false);
        }
    };

    document.querySelectorAll(".ai-btn").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.preventDefault();
            void runAction(button);
        });
    });

    if (retryBtn) {
        retryBtn.addEventListener("click", () => {
            if (lastAction) {
                void runAction(lastAction);
            }
        });
    }

    const acceptImprove = document.getElementById("ai-improve-accept");
    const rejectImprove = document.getElementById("ai-improve-reject");
    const improveBox = document.getElementById("ai-improve-suggestion");
    if (acceptImprove && rejectImprove && improveBox) {
        acceptImprove.addEventListener("click", () => {
            const field = document.getElementById("id_description");
            const textEl = document.getElementById("ai-improve-text");
            if (field && textEl) {
                field.value = textEl.textContent;
            }
            improveBox.hidden = true;
            setStatus("Description mise à jour. Enregistrez le formulaire pour conserver la modification.");
        });
        rejectImprove.addEventListener("click", () => {
            improveBox.hidden = true;
            setStatus("Suggestion rejetée. La description n'a pas été modifiée.");
        });
    }

    const acceptMeta = document.getElementById("ai-meta-accept");
    const rejectMeta = document.getElementById("ai-meta-reject");
    const metaBox = document.getElementById("ai-meta-suggestion");
    if (acceptMeta && rejectMeta && metaBox) {
        acceptMeta.addEventListener("click", () => {
            const category = document.getElementById("id_category");
            const level = document.getElementById("id_level");
            const tags = document.getElementById("id_tags");
            if (category && metaBox.dataset.category) {
                category.value = metaBox.dataset.category;
            }
            if (level && metaBox.dataset.level) {
                level.value = metaBox.dataset.level;
            }
            if (tags) {
                tags.value = metaBox.dataset.tags || "";
            }
            metaBox.hidden = true;
            setStatus("Catégorie, niveau et tags appliqués. Enregistrez pour les conserver.");
        });
        rejectMeta.addEventListener("click", () => {
            metaBox.hidden = true;
            setStatus("Suggestion rejetée. Les champs n'ont pas été modifiés.");
        });
    }
})();
