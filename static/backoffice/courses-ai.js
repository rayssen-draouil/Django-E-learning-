const button = document.getElementById("ai-generate");
if (button) {
    const status = document.getElementById("ai-status");
    const csrf = document.querySelector("[name=csrfmiddlewaretoken]").value;
    const originalText = button.textContent.trim();

    const setStatus = (message) => {
        status.textContent = message;
    };

    const poll = async (taskId) => {
        const url = button.dataset.statusUrl.replace("__TASK__", encodeURIComponent(taskId));
        for (let attempt = 0; attempt < 40; attempt += 1) {
            await new Promise((resolve) => setTimeout(resolve, 1500));
            const response = await fetch(url, { headers: { Accept: "application/json" } });
            const data = await response.json();
            if (data.status === "done") {
                document.getElementById("id_description").value = data.description;
                document.getElementById("id_objectives").value = data.objectives.join("\n");
                setStatus("Contenu généré. Vérifiez et modifiez avant d'enregistrer.");
                return;
            }
            if (data.status === "error") {
                throw new Error(data.error || "La génération IA a échoué.");
            }
        }
        throw new Error("La génération IA prend trop de temps.");
    };

    button.addEventListener("click", async () => {
        const title = document.getElementById("id_title").value.trim();
        if (title.length < 3) {
            setStatus("Saisissez d'abord un titre d'au moins 3 caractères.");
            return;
        }
        button.disabled = true;
        button.textContent = "Génération...";
        setStatus("Génération en cours...");
        try {
            const response = await fetch(button.dataset.url, {
                method: "POST",
                headers: { "X-CSRFToken": csrf, Accept: "application/json" },
                body: new URLSearchParams({
                    title,
                    level: document.getElementById("id_level").value,
                }),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || "Le service IA est indisponible.");
            }
            await poll(data.task_id);
        } catch (error) {
            setStatus(error.message);
        } finally {
            button.disabled = false;
            button.textContent = originalText;
        }
    });
}
