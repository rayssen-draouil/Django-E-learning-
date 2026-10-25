(function () {
    "use strict";

    const root = document.getElementById("ai-report-run");
    if (!root) {
        return;
    }

    const runButton = document.getElementById("ai-report-run-button");
    const confirmation = document.getElementById("ai-report-run-confirmation");
    const confirmButton = document.getElementById("ai-report-run-confirm");
    const cancelButton = document.getElementById("ai-report-run-cancel");
    const progressRegion = document.getElementById("ai-report-run-progress");
    const progressBar = document.getElementById("ai-report-run-progress-bar");
    const statusEl = document.getElementById("ai-report-run-status");
    const errorEl = document.getElementById("ai-report-run-error");

    const DEFAULT_TOTAL = 36;
    const POLL_INTERVAL_MS = 1500;
    const MAX_POLLS = 1200;

    const ERROR_MESSAGES = {
        missing_key: "La clé GROQ_API_KEY n'est pas configurée. L'évaluation n'a pas démarré et le rapport existant est conservé.",
        invalid_dataset: "Le jeu d'évaluation est illisible. Aucune évaluation n'a été lancée et le rapport existant est conservé.",
        dispatch_error: "Impossible de démarrer l'évaluation. Réessayez dans quelques instants.",
        unknown_task: "Impossible de retrouver cette évaluation.",
        invalid_task_id: "L'identifiant de l'évaluation est invalide.",
        storage_error: "Impossible de lire l'état de l'évaluation.",
        evaluation_error: "L'évaluation a échoué. Le rapport précédent est conservé.",
        internal_error: "L'évaluation a échoué. Le rapport précédent est conservé.",
    };

    let running = false;

    const csrfToken = () => {
        const input = root.querySelector("[name=csrfmiddlewaretoken]");
        return input ? input.value : "";
    };

    const setError = (message) => {
        if (!errorEl) {
            return;
        }
        if (message) {
            errorEl.hidden = false;
            errorEl.textContent = message;
        } else {
            errorEl.hidden = true;
            errorEl.textContent = "";
        }
    };

    const setBusy = (busy) => {
        running = busy;
        if (!runButton) {
            return;
        }
        runButton.disabled = busy;
        runButton.setAttribute("aria-busy", busy ? "true" : "false");
    };

    const setProgress = (done, total) => {
        const numericTotal = Number(total);
        const safeTotal = Number.isFinite(numericTotal) && numericTotal > 0
            ? numericTotal
            : DEFAULT_TOTAL;
        const numericDone = Number(done);
        const safeDone = Number.isFinite(numericDone)
            ? Math.min(Math.max(numericDone, 0), safeTotal)
            : 0;
        if (progressBar) {
            progressBar.max = safeTotal;
            progressBar.value = safeDone;
        }
        if (statusEl) {
            statusEl.textContent = "Évaluation en cours : " + safeDone + "/" + safeTotal;
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

    const wait = (duration) => new Promise((resolve) => {
        window.setTimeout(resolve, duration);
    });

    const statusErrorMessage = (errorCode) => {
        return ERROR_MESSAGES[errorCode]
            || "L'évaluation a échoué. Le rapport précédent est conservé.";
    };

    const poll = async (taskId, csrf) => {
        const template = root.dataset.statusUrl || "";
        if (!template) {
            throw new Error("La configuration du suivi est incomplète. Rechargez la page.");
        }
        const url = template.replace("__TASK__", encodeURIComponent(taskId));
        for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
            await wait(POLL_INTERVAL_MS);
            const response = await fetch(url, {
                method: "GET",
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
            const data = await readJson(
                response,
                "Impossible de récupérer l'état de l'évaluation."
            );
            if (data.state === "done") {
                setProgress(data.done, data.total);
                return data;
            }
            if (data.state === "error") {
                throw new Error(statusErrorMessage(data.error_code));
            }
            setProgress(data.done, data.total);
        }
        throw new Error("L'évaluation prend trop de temps. Rechargez la page pour vérifier le rapport.");
    };

    const start = async () => {
        if (running) {
            return;
        }
        const csrf = csrfToken();
        if (!csrf) {
            setError("Le jeton de sécurité est introuvable. Rechargez la page avant de réessayer.");
            return;
        }
        const runUrl = root.dataset.runUrl || "";
        if (!runUrl) {
            setError("La configuration du bouton est incomplète. Rechargez la page.");
            return;
        }

        if (confirmation) {
            confirmation.hidden = true;
        }
        setError("");
        setBusy(true);
        setProgress(0, DEFAULT_TOTAL);
        if (progressRegion) {
            progressRegion.hidden = false;
        }
        if (runButton) {
            runButton.textContent = "Évaluation en cours…";
        }

        try {
            const response = await fetch(runUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "X-CSRFToken": csrf,
                    Accept: "application/json",
                },
                body: new URLSearchParams({}),
            });
            const data = await readJson(
                response,
                "Le lancement de l'évaluation a échoué."
            );
            if (!data.task_id) {
                throw new Error("Le lancement n'a pas retourné d'identifiant de tâche.");
            }
            await poll(data.task_id, csrf);
            if (statusEl) {
                statusEl.textContent = "Évaluation terminée. Rechargement des résultats…";
            }
            window.setTimeout(() => {
                window.location.reload();
            }, 600);
        } catch (error) {
            if (progressRegion) {
                progressRegion.hidden = true;
            }
            setError(error.message || "L'évaluation a échoué.");
            setBusy(false);
            if (runButton) {
                runButton.textContent = "Relancer l'évaluation";
                runButton.focus();
            }
        }
    };

    if (runButton) {
        runButton.addEventListener("click", () => {
            setError("");
            if (confirmation) {
                confirmation.hidden = false;
                if (confirmButton) {
                    confirmButton.focus();
                }
            } else {
                void start();
            }
        });
    }

    if (confirmButton) {
        confirmButton.addEventListener("click", () => {
            void start();
        });
    }

    if (cancelButton) {
        cancelButton.addEventListener("click", () => {
            if (confirmation) {
                confirmation.hidden = true;
            }
            if (runButton) {
                runButton.focus();
            }
        });
    }
})();
