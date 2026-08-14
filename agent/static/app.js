(() => {
  const page = document.body.dataset.page;
  const sweetAlert = window.Swal;
  const confirmDialog = async ({
    title,
    text,
    icon = "question",
    confirmText = "ยืนยัน",
    danger = false
  }) => {
    if (!sweetAlert) return window.confirm(`${title}\n${text}`);
    const result = await sweetAlert.fire({
      title,
      text,
      icon,
      showCancelButton: true,
      reverseButtons: true,
      focusCancel: danger,
      confirmButtonText: confirmText,
      cancelButtonText: "ยกเลิก",
      buttonsStyling: false,
      customClass: {
        popup: "agent-swal-popup",
        title: "agent-swal-title",
        htmlContainer: "agent-swal-text",
        actions: "agent-swal-actions",
        confirmButton: danger ? "agent-swal-confirm danger" : "agent-swal-confirm",
        cancelButton: "agent-swal-cancel"
      }
    });
    return result.isConfirmed;
  };
  const notifyDialog = async (title, text, icon = "success") => {
    if (!sweetAlert) return;
    await sweetAlert.fire({
      title,
      text,
      icon,
      timer: icon === "success" ? 1500 : undefined,
      timerProgressBar: icon === "success",
      showConfirmButton: icon !== "success",
      confirmButtonText: "ตกลง",
      buttonsStyling: false,
      customClass: {
        popup: "agent-swal-popup",
        title: "agent-swal-title",
        htmlContainer: "agent-swal-text",
        confirmButton: "agent-swal-confirm"
      }
    });
  };

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const checkForUpdate = async () => {
    if (!csrfMeta?.content || !sweetAlert) return;
    try {
      const response = await fetch("/api/update/status", {
        credentials: "same-origin",
        cache: "no-store"
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok || !payload.update_available) return;
      const noticeKey = `agent-update-${payload.latest_version}`;
      if (window.sessionStorage.getItem(noticeKey)) return;
      window.sessionStorage.setItem(noticeKey, "shown");
      const confirmed = await confirmDialog({
        title: `มี Agent v${payload.latest_version}`,
        text: `เครื่องนี้ใช้ v${payload.current_version} ต้องการดาวน์โหลดและอัปเดตอัตโนมัติหรือไม่?`,
        icon: "info",
        confirmText: "อัปเดตตอนนี้"
      });
      if (!confirmed) return;
      sweetAlert.fire({
        title: "กำลังดาวน์โหลดอัปเดต",
        text: "ระบบกำลังตรวจสอบ SHA-256 และเตรียมสลับเวอร์ชัน…",
        allowOutsideClick: false,
        showConfirmButton: false,
        didOpen: () => sweetAlert.showLoading()
      });
      const body = new FormData();
      body.append("csrf_token", csrfMeta.content);
      const installResponse = await fetch("/api/update/install", {
        method: "POST",
        body,
        credentials: "same-origin"
      });
      const install = await installResponse.json();
      if (!installResponse.ok || !install.ok) {
        throw new Error(install.error || "Update failed");
      }
      await sweetAlert.fire({
        title: `กำลังเริ่ม v${install.version}`,
        text: "ตัวใหม่กำลังหยุดเวอร์ชันเก่าและรับช่วงทำงาน หน้านี้จะโหลดใหม่อัตโนมัติ",
        icon: "success",
        showConfirmButton: false,
        allowOutsideClick: false,
        timer: 3500
      });
      const deadline = Date.now() + 60000;
      const waitForUpdate = async () => {
        if (Date.now() >= deadline) {
          window.location.reload();
          return;
        }
        try {
          const health = await fetch(`/healthz?t=${Date.now()}`, {cache: "no-store"});
          const status = await health.json();
          if (status.version === install.version) {
            window.location.reload();
            return;
          }
        } catch (_) {}
        window.setTimeout(waitForUpdate, 1500);
      };
      window.setTimeout(waitForUpdate, 1500);
    } catch (error) {
      await notifyDialog("อัปเดตอัตโนมัติไม่สำเร็จ", error.message, "error");
    }
  };
  if (csrfMeta?.content) window.setTimeout(checkForUpdate, 1200);

  if (page === "database") {
    const form = document.querySelector("#db-form");
    const testButton = document.querySelector("#test-db");
    const saveButton = document.querySelector("#save-db");
    const result = document.querySelector("#db-test-result");
    const token = document.querySelector("#test-token");

    form.addEventListener("input", () => {
      token.value = "";
      saveButton.disabled = true;
      result.className = "test-result span-2";
      result.textContent = "ข้อมูลเปลี่ยนแล้ว กรุณาทดสอบการเชื่อมต่ออีกครั้ง";
    });

    testButton.addEventListener("click", async () => {
      if (!form.reportValidity()) return;
      testButton.disabled = true;
      saveButton.disabled = true;
      result.className = "test-result span-2 pending";
      result.textContent = "กำลังทดสอบการเชื่อมต่อ…";
      try {
        const response = await fetch("/setup/database/test", {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin"
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Connection failed");
        token.value = payload.test_token;
        saveButton.disabled = false;
        result.className = "test-result span-2 success";
        const warning = payload.details.warning ? ` • ${payload.details.warning}` : "";
        result.textContent = `เชื่อมต่อสำเร็จ • MariaDB ${payload.details.version} • ${payload.details.database}${warning}`;
      } catch (error) {
        token.value = "";
        result.className = "test-result span-2 error";
        result.textContent = `เชื่อมต่อไม่สำเร็จ: ${error.message}`;
      } finally {
        testButton.disabled = false;
      }
    });
  }

  if (page === "logs") {
    const terminal = document.querySelector("#log-terminal");
    const state = document.querySelector("#worker-state");
    const error = document.querySelector("#worker-error");
    const count = document.querySelector("#poll-count");
    const lastPoll = document.querySelector("#last-poll");
    const csrf = document.querySelector("#worker-csrf").value;
    // Bound both the rendered DOM and the background-tab queue. Log entries
    // still flow like a terminal, but browser memory cannot grow forever.
    const maxTerminalLines = 500;
    const pendingTerminalEntries = [];
    let terminalLineCount = 0;
    let terminalFlushScheduled = false;
    const preview = document.querySelector("#preview-json");
    let previewSignature = "";

    const formatBangkokDateTime = (value) => {
      if (!value) return "ไม่ทราบเวลา";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      const parts = Object.fromEntries(
        new Intl.DateTimeFormat("en-CA", {
          timeZone: "Asia/Bangkok",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hourCycle: "h23"
        }).formatToParts(date).map(({type, value: partValue}) => [type, partValue])
      );
      return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} (UTC+7)`;
    };

    const jsonPrimitive = (value) => value === null ? "null" : JSON.stringify(value);
    const buildJsonNode = (key, value) => {
      const objectLike = value !== null && typeof value === "object";
      if (!objectLike) {
        const row = document.createElement("div");
        row.className = "json-row";
        const keyNode = document.createElement("span");
        keyNode.className = "json-key";
        keyNode.textContent = `${key}: `;
        const valueNode = document.createElement("span");
        valueNode.className = `json-value json-${value === null ? "null" : typeof value}`;
        valueNode.textContent = jsonPrimitive(value);
        row.append(keyNode, valueNode);
        return row;
      }
      const details = document.createElement("details");
      details.className = "json-branch";
      details.open = false;
      const summary = document.createElement("summary");
      const size = Array.isArray(value) ? value.length : Object.keys(value).length;
      summary.textContent = `${key}: ${Array.isArray(value) ? `[${size}]` : `{${size}}`}`;
      details.appendChild(summary);
      const children = document.createElement("div");
      children.className = "json-children";
      Object.entries(value).forEach(([childKey, childValue]) => {
        children.appendChild(buildJsonNode(Array.isArray(value) ? `[${childKey}]` : childKey, childValue));
      });
      details.appendChild(children);
      return details;
    };

    const renderPreviews = (items) => {
      preview.replaceChildren();
      if (!items.length) {
        preview.textContent = "ยังไม่มีข้อมูล preview — ระบบจะสร้างเมื่อพบ INSERT / UPDATE / DELETE หลังจบ baseline รอบแรก";
        return;
      }
      items.forEach((item, index) => {
        const vn = item?.trigger?.vn || "ไม่มี VN";
        const generated = formatBangkokDateTime(item?.generated_at);
        preview.appendChild(buildJsonNode(`Preview ${index + 1} • VN ${vn} • ${generated}`, item));
      });
    };

    const flushTerminalEntries = () => {
      terminalFlushScheduled = false;
      if (!pendingTerminalEntries.length) return;
      if (terminal.querySelector(".muted")) {
        terminal.replaceChildren();
        terminalLineCount = 0;
      }

      const entries = pendingTerminalEntries.splice(0);
      const fragment = document.createDocumentFragment();
      entries.forEach((entry) => {
        const line = document.createElement("span");
        line.className = `log-line level-${entry.level.toLowerCase()}`;
        const time = new Date(entry.timestamp).toLocaleTimeString("th-TH", {
          hour12: false,
          timeZone: "Asia/Bangkok"
        });
        line.textContent = `${time}  ${entry.level.padEnd(7)}  ${entry.message}\n`;
        fragment.appendChild(line);
      });
      terminal.appendChild(fragment);
      terminalLineCount += entries.length;

      const overflow = terminalLineCount - maxTerminalLines;
      for (let index = 0; index < overflow; index += 1) {
        terminal.firstElementChild?.remove();
      }
      terminalLineCount = Math.min(terminalLineCount, maxTerminalLines);
      terminal.scrollTop = terminal.scrollHeight;
    };

    const addLine = (entry) => {
      pendingTerminalEntries.push(entry);
      if (pendingTerminalEntries.length > maxTerminalLines) {
        pendingTerminalEntries.splice(
          0,
          pendingTerminalEntries.length - maxTerminalLines
        );
      }
      if (!terminalFlushScheduled) {
        terminalFlushScheduled = true;
        window.requestAnimationFrame(flushTerminalEntries);
      }
    };

    const refreshStatus = async () => {
      try {
        const response = await fetch("/api/status", {credentials: "same-origin"});
        if (!response.ok) return;
        const payload = await response.json();
        state.textContent = payload.worker.running ? "RUNNING" : "STOPPED";
        state.className = payload.worker.running ? "state-running" : "state-stopped";
        error.textContent = payload.worker.last_error || "No active error";
        count.textContent = payload.worker.poll_count;
        lastPoll.textContent = payload.worker.last_poll_at
          ? formatBangkokDateTime(payload.worker.last_poll_at)
          : "Waiting for first poll";
      } catch (_) { /* status retries automatically */ }
    };

    async function refreshPreviews() {
      if (!preview) return;
      try {
        const response = await fetch("/api/previews", {credentials: "same-origin"});
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Preview unavailable");
        const signature = JSON.stringify(payload.items);
        if (signature === previewSignature) return;
        previewSignature = signature;
        renderPreviews(payload.items);
      } catch (err) {
        preview.textContent = `อ่าน preview ไม่สำเร็จ: ${err.message}`;
      }
    }

    const connectLogStream = async () => {
      let brokerSequence = "";
      try {
        const response = await fetch("/api/logs/recent", {
          credentials: "same-origin"
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          throw new Error(payload.error || "Persisted logs unavailable");
        }
        payload.items.forEach(addLine);
        brokerSequence = String(payload.broker_sequence ?? "");
      } catch (err) {
        addLine({
          timestamp: new Date().toISOString(),
          level: "WARNING",
          message: `อ่าน Log ที่บันทึกไว้ไม่สำเร็จ: ${err.message}`
        });
      }

      const streamUrl = brokerSequence
        ? `/api/logs/stream?after=${encodeURIComponent(brokerSequence)}`
        : "/api/logs/stream";
      const stream = new EventSource(streamUrl);
      stream.onmessage = (event) => {
        const entry = JSON.parse(event.data);
        addLine(entry);
        if (
          entry.message.includes("DRY RUN:") ||
          entry.message.includes("API POST batch acknowledged:")
        ) {
          if (!document.hidden) refreshPreviews();
        }
      };
      stream.onerror = () => {
        addLine({timestamp: new Date().toISOString(), level: "WARNING", message: "Log stream disconnected; browser will retry"});
      };
    };
    connectLogStream();
    setInterval(() => {
      if (!document.hidden) refreshStatus();
    }, 3000);
    setInterval(() => {
      if (!document.hidden) refreshPreviews();
    }, 3000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshStatus();
        refreshPreviews();
      }
    });
    refreshStatus();
    refreshPreviews();

    document.querySelectorAll("[data-worker-action]").forEach((button) => {
      button.addEventListener("click", async () => {
        const action = button.dataset.workerAction;
        const actionDialogs = {
          start: {
            title: "เริ่มการทำงานของ Agent?",
            text: "Agent จะเริ่มตรวจสอบการเปลี่ยนแปลงจากฐานข้อมูล HOSxP",
            confirmText: "เริ่มทำงาน"
          },
          restart: {
            title: "เริ่ม Agent ใหม่?",
            text: "Worker จะหยุดชั่วครู่และเริ่มทำงานใหม่ โดยคิว VN ที่รอส่งจะไม่หาย",
            confirmText: "Restart"
          },
          stop: {
            title: "หยุด Worker ชั่วคราว?",
            text: "หน้าเว็บยังเปิดได้ แต่ Agent จะไม่ตรวจข้อมูลจนกว่าจะกด Start",
            icon: "warning",
            confirmText: "หยุด Worker",
            danger: true
          },
          shutdown: {
            title: "ปิด Agent ทั้งระบบ?",
            text: "Web service และ Worker จะหยุดทำงาน หน้าเว็บนี้จะเข้าไม่ได้จนกว่าจะเปิด EXE ใหม่",
            icon: "warning",
            confirmText: "Exit Agent",
            danger: true
          }
        };
        if (!await confirmDialog(actionDialogs[action])) return;
        button.disabled = true;
        const body = new FormData();
        body.append("csrf_token", csrf);
        try {
          const response = await fetch(`/api/worker/${action}`, {
            method: "POST", body, credentials: "same-origin"
          });
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || "Operation failed");
          if (payload.shutting_down) {
            addLine({timestamp: new Date().toISOString(), level: "INFO", message: "Agent is shutting down safely"});
            await notifyDialog(
              "กำลังปิด Agent",
              "ระบบกำลังหยุด Web service และ Worker อย่างปลอดภัย",
              "success"
            );
            return;
          }
          await refreshStatus();
          const successMessages = {
            start: ["เริ่ม Agent แล้ว", "Worker เริ่มตรวจสอบข้อมูลเรียบร้อย"],
            restart: ["Restart สำเร็จ", "Worker เริ่มทำงานใหม่เรียบร้อย"],
            stop: ["หยุด Worker แล้ว", "กด Start เมื่อต้องการเริ่มตรวจข้อมูลอีกครั้ง"]
          };
          await notifyDialog(...successMessages[action]);
        } catch (err) {
          addLine({timestamp: new Date().toISOString(), level: "ERROR", message: err.message});
          await notifyDialog("ดำเนินการไม่สำเร็จ", err.message, "error");
        } finally {
          button.disabled = false;
        }
      });
    });
    document.querySelector("#clear-log").addEventListener("click", () => {
      pendingTerminalEntries.length = 0;
      terminal.replaceChildren();
      terminalLineCount = 0;
    });
    const copyLogButton = document.querySelector("#copy-log");
    const copyLogLabel = document.querySelector("#copy-log-label");
    if (copyLogButton) {
      copyLogButton.addEventListener("click", async () => {
        flushTerminalEntries();
        const logText = Array.from(terminal.querySelectorAll(".log-line"))
          .map((line) => line.textContent)
          .join("");
        copyLogButton.classList.remove("copy-success", "copy-error");
        if (!logText.trim()) {
          copyLogButton.classList.add("copy-error");
          copyLogLabel.textContent = "ไม่มี Log";
          window.setTimeout(() => {
            copyLogButton.classList.remove("copy-error");
            copyLogLabel.textContent = "Copy logs";
          }, 1800);
          return;
        }
        try {
          if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(logText);
          } else {
            const helper = document.createElement("textarea");
            helper.value = logText;
            helper.setAttribute("readonly", "");
            helper.style.position = "fixed";
            helper.style.opacity = "0";
            document.body.appendChild(helper);
            helper.select();
            const copied = document.execCommand("copy");
            helper.remove();
            if (!copied) throw new Error("Copy unavailable");
          }
          copyLogButton.classList.add("copy-success");
          copyLogLabel.textContent = "คัดลอกแล้ว";
        } catch (_) {
          copyLogButton.classList.add("copy-error");
          copyLogLabel.textContent = "คัดลอกไม่สำเร็จ";
        }
        window.setTimeout(() => {
          copyLogButton.classList.remove("copy-success", "copy-error");
          copyLogLabel.textContent = "Copy logs";
        }, 1800);
      });
    }
    const refreshPreviewButton = document.querySelector("#refresh-preview");
    if (refreshPreviewButton) refreshPreviewButton.addEventListener("click", refreshPreviews);
    const togglePreviewButton = document.querySelector("#toggle-preview");
    if (togglePreviewButton && preview) {
      togglePreviewButton.addEventListener("click", () => {
        const collapsed = preview.classList.toggle("is-collapsed");
        togglePreviewButton.textContent = collapsed ? "แสดง JSON" : "ซ่อน JSON";
        togglePreviewButton.setAttribute("aria-expanded", String(!collapsed));
      });
    }
    const collapseAllPreviewButton = document.querySelector("#collapse-all-preview");
    if (collapseAllPreviewButton && preview) {
      collapseAllPreviewButton.addEventListener("click", () => {
        preview.querySelectorAll("details").forEach((details) => { details.open = false; });
      });
    }
    const expandAllPreviewButton = document.querySelector("#expand-all-preview");
    if (expandAllPreviewButton && preview) {
      expandAllPreviewButton.addEventListener("click", () => {
        preview.querySelectorAll("details").forEach((details) => { details.open = true; });
      });
    }
    const clearPreviewButton = document.querySelector("#clear-preview");
    if (clearPreviewButton) {
      clearPreviewButton.addEventListener("click", async () => {
        const confirmed = await confirmDialog({
          title: "ล้างประวัติ Payload?",
          text: "ข้อมูล Preview ทั้งหมดจะถูกลบ แต่การทำงานและคิว VN จะไม่ถูกล้าง",
          icon: "warning",
          confirmText: "ล้างประวัติ",
          danger: true
        });
        if (!confirmed) return;
        clearPreviewButton.disabled = true;
        const body = new FormData();
        body.append("csrf_token", csrf);
        try {
          const response = await fetch("/api/previews/clear", {
            method: "POST", body, credentials: "same-origin"
          });
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || "Clear failed");
          await refreshPreviews();
          await notifyDialog(
            "ล้างประวัติแล้ว",
            "ประวัติ Payload ถูกลบเรียบร้อย",
            "success"
          );
        } catch (err) {
          addLine({timestamp: new Date().toISOString(), level: "ERROR", message: `ล้าง Preview ไม่สำเร็จ: ${err.message}`});
          await notifyDialog("ล้างประวัติไม่สำเร็จ", err.message, "error");
        } finally {
          clearPreviewButton.disabled = false;
        }
      });
    }
  }
})();
