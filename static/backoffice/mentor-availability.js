(() => {
  const MIN_DURATION = 15;
  const MAX_DURATION = 240;

  // Dismissible alerts
  document.querySelectorAll("[data-dismiss-alert]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const alert = btn.closest(".availability-alert");
      if (alert) alert.remove();
    });
  });

  const parseMinutes = (timeStr) => {
    if (!timeStr || typeof timeStr !== "string") return null;
    const parts = timeStr.trim().split(":");
    if (parts.length < 2) return null;
    const hours = Number(parts[0]);
    const minutes = Number(parts[1]);
    if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null;
    if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
    return hours * 60 + minutes;
  };

  const setFieldError = (form, fieldName, message) => {
    const wrapper = form.querySelector(`[data-field-wrapper="${fieldName}"]`);
    const errorEl = form.querySelector(`[data-field-error="${fieldName}"]`);
    const input = form.querySelector(`[name="${fieldName}"]`);
    if (wrapper) wrapper.classList.add("has-error");
    if (input) {
      input.classList.add("input-invalid");
      input.setAttribute("aria-invalid", "true");
    }
    if (errorEl) {
      errorEl.textContent = message;
    }
  };

  const clearFieldError = (form, fieldName) => {
    const wrapper = form.querySelector(`[data-field-wrapper="${fieldName}"]`);
    const errorEl = form.querySelector(`[data-field-error="${fieldName}"]`);
    const input = form.querySelector(`[name="${fieldName}"]`);
    if (wrapper) wrapper.classList.remove("has-error");
    if (input) {
      input.classList.remove("input-invalid");
      input.removeAttribute("aria-invalid");
    }
    if (errorEl) {
      errorEl.textContent = "";
    }
  };

  const initAvailabilityForm = (form) => {
    const dateInput = form.querySelector('[name="date"]');
    const startInput = form.querySelector('[name="start_time"]');
    const endInput = form.querySelector('[name="end_time"]');
    const topicSelect = form.querySelector('[name="topic"]');
    const customTopicGroup = form.querySelector("[data-custom-topic-group]");
    const customTopicInput = form.querySelector('[name="custom_topic"]');
    const formatRadios = form.querySelectorAll("[data-session-type-radio]");
    const onlineInfo = form.querySelector("[data-online-info]");
    const locationGroup = form.querySelector("[data-location-group]");
    const durationBadge = form.querySelector("[data-duration-preview]");
    const submitBtn = form.querySelector("[data-submit-button]");

    const updateDurationPreview = () => {
      if (!durationBadge || !startInput || !endInput) return;
      const startMin = parseMinutes(startInput.value);
      const endMin = parseMinutes(endInput.value);
      if (startMin === null || endMin === null) {
        durationBadge.textContent = "15–240 min";
        durationBadge.classList.remove("duration-invalid");
        return;
      }
      const diff = endMin - startMin;
      if (diff <= 0) {
        durationBadge.textContent = "Invalid range";
        durationBadge.classList.add("duration-invalid");
      } else if (diff < MIN_DURATION || diff > MAX_DURATION) {
        durationBadge.textContent = `${diff} min (limit: 15–240 min)`;
        durationBadge.classList.add("duration-invalid");
      } else {
        durationBadge.textContent = `${diff} min`;
        durationBadge.classList.remove("duration-invalid");
      }
    };

    const syncTopicState = () => {
      if (!topicSelect || !customTopicGroup) return;
      const isOther = topicSelect.value === "Other";
      customTopicGroup.hidden = !isOther;
      if (customTopicInput) {
        customTopicInput.required = isOther;
        if (isOther) {
          customTopicInput.setAttribute("aria-required", "true");
        } else {
          customTopicInput.removeAttribute("aria-required");
          clearFieldError(form, "custom_topic");
        }
      }
    };

    const syncFormatState = () => {
      const checked = form.querySelector('[name="session_type"]:checked');
      const value = checked ? checked.value : "online";
      form.querySelectorAll("[data-format-card]").forEach((card) => {
        card.classList.toggle("selected", card.dataset.formatCard === value);
      });
      if (onlineInfo) onlineInfo.hidden = value !== "online";
      if (locationGroup) locationGroup.hidden = value !== "in_person";
    };

    ["date", "start_time", "end_time", "topic", "custom_topic", "location"].forEach((name) => {
      const input = form.querySelector(`[name="${name}"]`);
      if (input) {
        input.addEventListener("input", () => clearFieldError(form, name));
        input.addEventListener("change", () => clearFieldError(form, name));
      }
    });

    if (startInput) {
      startInput.addEventListener("input", updateDurationPreview);
      startInput.addEventListener("change", updateDurationPreview);
    }
    if (endInput) {
      endInput.addEventListener("input", updateDurationPreview);
      endInput.addEventListener("change", updateDurationPreview);
    }
    if (topicSelect) {
      topicSelect.addEventListener("change", syncTopicState);
    }
    formatRadios.forEach((radio) => {
      radio.addEventListener("change", () => {
        clearFieldError(form, "session_type");
        syncFormatState();
      });
    });

    updateDurationPreview();
    syncTopicState();
    syncFormatState();

    // Focus first server-side invalid field if present
    const firstServerInvalid = form.querySelector('[aria-invalid="true"]');
    if (firstServerInvalid && !form.closest("[hidden]")) {
      firstServerInvalid.focus();
    }

    let isSubmitting = false;
    form.addEventListener("submit", (event) => {
      if (isSubmitting) {
        event.preventDefault();
        return;
      }

      [
        "date",
        "start_time",
        "end_time",
        "topic",
        "custom_topic",
        "session_type",
        "location",
      ].forEach((name) => clearFieldError(form, name));

      let firstInvalidField = null;
      const markInvalid = (fieldName, message) => {
        setFieldError(form, fieldName, message);
        if (!firstInvalidField) {
          firstInvalidField = form.querySelector(`[name="${fieldName}"]`);
        }
      };

      const dateVal = dateInput ? dateInput.value.trim() : "";
      const minDate = dateInput ? dateInput.getAttribute("min") : "";
      if (!dateVal) {
        markInvalid("date", "Choose a date so students know when you're available.");
      } else if (minDate && dateVal < minDate) {
        markInvalid("date", "That date has already passed. Please select today or a future date.");
      }

      const startVal = startInput ? startInput.value.trim() : "";
      const endVal = endInput ? endInput.value.trim() : "";
      if (!startVal) {
        markInvalid("start_time", "When does your availability begin?");
      }
      if (!endVal) {
        markInvalid("end_time", "Your end time must be later than your start time.");
      } else if (startVal) {
        const startMin = parseMinutes(startVal);
        const endMin = parseMinutes(endVal);
        if (startMin === null || endMin === null || endMin <= startMin) {
          markInvalid("end_time", "Your end time must be later than your start time.");
        } else {
          const duration = endMin - startMin;
          if (duration < MIN_DURATION) {
            markInvalid("end_time", `Choose a session lasting at least ${MIN_DURATION} minutes.`);
          } else if (duration > MAX_DURATION) {
            markInvalid("end_time", "Sessions cannot exceed 4 hours (240 minutes).");
          }
        }
      }

      const topicVal = topicSelect ? topicSelect.value.trim() : "";
      if (!topicVal) {
        markInvalid("topic", "Choose a mentoring topic to help students find the right session.");
      } else if (topicVal === "Other") {
        const customVal = customTopicInput ? customTopicInput.value.trim() : "";
        if (!customVal) {
          markInvalid("custom_topic", "Tell us which topic you want to mentor.");
        } else if (customVal.length > 120) {
          markInvalid("custom_topic", "Custom topic must be 120 characters or fewer.");
        }
      }

      const checkedFormat = form.querySelector('[name="session_type"]:checked');
      if (!checkedFormat) {
        markInvalid("session_type", "Please select whether this session is Online or In-person.");
      }

      if (firstInvalidField) {
        event.preventDefault();
        firstInvalidField.focus();
        return;
      }

      isSubmitting = true;
      if (submitBtn) {
        submitBtn.disabled = true;
        const spinner = submitBtn.querySelector(".btn-spinner");
        const label = submitBtn.querySelector("[data-submit-label]");
        if (spinner) spinner.hidden = false;
        if (label && submitBtn.dataset.loadingText) {
          label.textContent = submitBtn.dataset.loadingText;
        }
      }
    });
  };

  document.querySelectorAll("[data-availability-form]").forEach(initAvailabilityForm);

  // Create Availability Modal interactions
  const createModal = document.getElementById("create-availability-modal");
  if (createModal) {
    document.querySelectorAll("[data-open-create-modal]").forEach((trigger) => {
      trigger.addEventListener("click", (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        createModal.hidden = false;
        const firstInput = createModal.querySelector("input, select");
        if (firstInput) firstInput.focus();
      });
    });

    createModal.querySelectorAll("[data-close-modal]").forEach((closeBtn) => {
      closeBtn.addEventListener("click", (event) => {
        event.preventDefault();
        createModal.hidden = true;
      });
    });

    createModal.addEventListener("click", (event) => {
      if (event.target === createModal) {
        createModal.hidden = true;
      }
    });
  }

  // Delete Confirmation Modal interactions
  const deleteModal = document.getElementById("delete-availability-modal");
  if (deleteModal) {
    const deleteForm = document.getElementById("delete-availability-form");
    const dateTarget = deleteModal.querySelector("[data-delete-slot-date]");
    const timeTarget = deleteModal.querySelector("[data-delete-slot-time]");
    const topicTarget = deleteModal.querySelector("[data-delete-slot-topic]");
    const confirmBtn = deleteModal.querySelector("[data-delete-confirm-btn]");

    document.querySelectorAll("[data-delete-trigger]").forEach((trigger) => {
      trigger.addEventListener("click", (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (deleteForm && trigger.dataset.deleteUrl) {
          deleteForm.action = trigger.dataset.deleteUrl;
        }
        if (dateTarget) dateTarget.textContent = trigger.dataset.slotDate || "";
        if (timeTarget) timeTarget.textContent = trigger.dataset.slotTime || "";
        if (topicTarget) topicTarget.textContent = trigger.dataset.slotTopic || "";
        deleteModal.hidden = false;
        if (confirmBtn) confirmBtn.focus();
      });
    });

    deleteModal.querySelectorAll("[data-close-delete-modal]").forEach((closeBtn) => {
      closeBtn.addEventListener("click", () => {
        deleteModal.hidden = true;
      });
    });

    deleteModal.addEventListener("click", (event) => {
      if (event.target === deleteModal) {
        deleteModal.hidden = true;
      }
    });
  }

  // Delete form loading state (both modal and standalone page)
  document.querySelectorAll("[data-delete-form]").forEach((form) => {
    let deleting = false;
    form.addEventListener("submit", (event) => {
      if (deleting) {
        event.preventDefault();
        return;
      }
      deleting = true;
      const btn = form.querySelector("[data-delete-confirm-btn]");
      if (btn) {
        btn.disabled = true;
        const spinner = btn.querySelector(".btn-spinner");
        const label = btn.querySelector("[data-submit-label]");
        if (spinner) spinner.hidden = false;
        if (label && btn.dataset.loadingText) {
          label.textContent = btn.dataset.loadingText;
        }
      }
    });
  });

  // Escape key closes open modals
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (createModal && !createModal.hidden) createModal.hidden = true;
      if (deleteModal && !deleteModal.hidden) deleteModal.hidden = true;
    }
  });
})();
