"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const SVG_NS = "http://www.w3.org/2000/svg";
  const booleanSettings = new Set(["auto_download", "crafting_schedule_enabled", "cafe_schedule_enabled", "cafe_invite_enabled", "close_app_when_idle", "lessons_enabled_in_daily"]);
  const stringSettings = new Set(["cafe_invite_student", "lessons_strategy"]);
  const settingNames = ["auto_download", "poll_interval", "startup_timeout", "download_timeout", "unknown_timeout", "crafting_schedule_enabled", "close_app_when_idle", "cafe_schedule_enabled", "cafe_invite_enabled", "cafe_invite_student", "lessons_strategy", "lessons_max_tickets", "lessons_locations", "lessons_enabled_in_daily"];
  const fields = Object.fromEntries(settingNames.map((name) => [name, $(name.replaceAll("_", "-"))]));
  let status = null;
  let connected = false;
  let pending = 0;
  let polling = null;
  let refreshPromise = null;
  let frameVersion = null;
  let frameLoaded = false;
  let mapData = null;
  let mapLoading = false;
  let selectedButton = null;
  let settingsLoaded = false;
  let settingsDirty = false;
  let logSignature = "";
  let queueSignature = "";
  let historySignature = "";
  let popupSignature = "";
  let popupsLoading = false;
  let popups = [];
  let popupLimit = 6;
  let actionsSignature = "";
  let actionsLoading = false;
  let importantActions = [];
  let actionsLimit = 15;
  let noticeTimer = null;

  const taskName = (task) => ({ restart: "Restart", daily: "Daily", club: "Club", crafting: "Crafting", cafe: "Café", lessons: "Lessons" }[task] || task || "—");
  const isRunning = () => Boolean(status && (status.state === "running" || status.current_job));
  const queue = () => (Array.isArray(status?.queue) ? status.queue : []);
  const isDemo = () => status?.demo === true;

  function showError(message) {
    $("error-message").textContent = message;
    $("error-banner").hidden = false;
  }

  function showNotice(message) {
    clearTimeout(noticeTimer);
    $("notice").textContent = message;
    $("notice").hidden = false;
    noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 5000);
  }

  function dateValue(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function clockTime(value) {
    return dateValue(value)?.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) || "—";
  }

  function shortTime(value) {
    return dateValue(value)?.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) || "—";
  }

  function duration(seconds) {
    if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "—";
    seconds = Math.max(0, Math.floor(seconds));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${String(remainder).padStart(2, "0")}s` : `${remainder}s`;
  }

  function renderElapsed() {
    if (!status) return;
    let elapsed = status.duration_seconds;
    const started = dateValue(status.current_job?.started_at || status.started_at);
    if (isRunning() && started) elapsed = (Date.now() - started.getTime()) / 1000;
    $("elapsed").textContent = duration(elapsed);
    renderSchedule();
  }

  function renderSchedule() {
    const crafting = status?.schedule?.crafting;
    const craftEnabled = crafting?.enabled ?? status?.config?.crafting_schedule_enabled ?? false;
    $("crafting-status").textContent = crafting?.disabled_reason
      ? `disabled: ${crafting.disabled_reason}. fix the setup in game, then queue crafting.`
      : crafting?.retry_paused ? "retries paused. check the log, then resume the queue."
      : !craftEnabled ? "schedule off. queue crafting for a single visit."
      : crafting?.not_before && dateValue(crafting.not_before)?.getTime() > Date.now() && crafting.consecutive_failures
        ? `retry at ${shortTime(crafting.not_before)}`
        : status?.queue_paused ? "timers saved. collection waits while the queue is paused."
        : "timers saved. finished crafts go through the queue, then get refilled.";
    const craftJobs = document.createDocumentFragment();
    (crafting?.jobs || []).forEach((job) => {
      const item = document.createElement("li");
      const due = dateValue(job.due_at);
      const remaining = due ? Math.max(0, Math.ceil((due.getTime() - Date.now()) / 1000)) : null;
      item.textContent = `${job.label} · ${shortTime(job.due_at)}${remaining !== null ? ` · ${remaining ? duration(remaining) : "due now"}` : ""}${!craftEnabled ? " · schedule off" : ""}`;
      craftJobs.append(item);
    });
    $("crafting-jobs").replaceChildren(craftJobs);
    const schedule = status?.schedule?.cafe;
    const enabled = schedule?.enabled ?? status?.config?.cafe_schedule_enabled ?? false;
    $("cafe-schedule-title").textContent = enabled ? "next café visit" : "café schedule";
    const summary = $("cafe-schedule-status");
    if (!enabled) {
      summary.textContent = "schedule off";
    } else if (schedule?.retry_paused) {
      const failures = Number(schedule.consecutive_failures) || 0;
      summary.textContent = `Retries paused${failures ? ` after ${failures} failed run${failures === 1 ? "" : "s"}` : ""}`;
    } else {
      const due = dateValue(schedule?.next_due_at);
      if (!due) summary.textContent = "every 3 hours · waiting for the next visit";
      else {
        const remaining = Math.ceil((due.getTime() - Date.now()) / 1000);
        if (remaining <= 0) summary.textContent = status.queue_paused ? "due now · queue is paused" : "due now · goes in the queue";
        else {
          const totalMinutes = Math.ceil(remaining / 60);
          const hours = Math.floor(totalMinutes / 60);
          const minutes = totalMinutes % 60;
          const countdown = hours ? `${hours}h ${minutes}m` : `${minutes}m`;
          summary.textContent = `${shortTime(due.toISOString())} · in ${countdown}`;
        }
      }
    }
  }

  function stateBadge(element, state, label) {
    const normalized = ["running", "success", "failed", "stopped", "paused"].includes(state) ? state : "neutral";
    element.className = `badge ${normalized}`;
    element.textContent = label;
  }

  function renderControls() {
    const active = isRunning();
    const paused = Boolean(status?.queue_paused);
    const unavailable = !connected || pending > 0 || isDemo();
    const queued = queue().length > 0;
    $("run-restart").disabled = unavailable;
    $("run-daily").disabled = unavailable;
    $("run-cafe").disabled = unavailable;
    $("run-club").disabled = unavailable;
    $("run-crafting").disabled = unavailable;
    $("run-lessons").disabled = unavailable;
    $("run-restart").querySelector("span").textContent = "queue restart";
    $("run-daily").textContent = "queue daily";
    $("run-cafe").textContent = "queue café";
    $("stop-run").disabled = unavailable || !active;
    $("capture").disabled = unavailable || active;
    $("pause-queue").hidden = paused;
    $("pause-queue").disabled = unavailable;
    $("pause-queue").textContent = active ? "pause after this task" : "pause queue";
    $("resume-queue").hidden = !paused;
    $("resume-queue").disabled = unavailable;
    $("settings-fields").disabled = unavailable || active || queued;
    $("settings-lock-note").hidden = !active && !queued;
    $("save-settings").disabled = unavailable || active || queued || !settingsDirty;
    fields.cafe_invite_student.disabled = unavailable || !fields.cafe_invite_enabled.checked;
    fields.cafe_invite_student.required = fields.cafe_invite_enabled.checked;
    $("lessons-strategy-description").textContent = fields.lessons_strategy.value === "school_rank"
      ? "lowest rank first, then lowest XP. recheck after each ticket. pick the room with the most students; higher owned relationships break ties."
      : "check every location first. use tickets on rooms with the most owned students; higher relationships break ties.";
    $("daily-plan").textContent = status?.config?.lessons_enabled_in_daily === false
      ? "daily does restart → club → café. club is stubbed; lessons are off for daily."
      : "daily does restart → club → café → lessons. club is stubbed for now; the reset test comes next.";
    $("map-toggle").disabled = !frameLoaded || !mapData;
    document.querySelectorAll("[data-cancel-job]").forEach((button) => { button.disabled = unavailable; });
  }

  function renderSettings(config) {
    if (!config || (settingsLoaded && settingsDirty)) return;
    settingNames.forEach((name) => {
      if (booleanSettings.has(name)) fields[name].checked = Boolean(config[name] ?? (name === "lessons_enabled_in_daily"));
      else if (name === "lessons_locations") fields[name].value = Array.isArray(config[name]) ? config[name].join("\n") : "";
      else fields[name].value = config[name] ?? ({ lessons_strategy: "relationship", lessons_max_tickets: 0 }[name] ?? "");
    });
    settingsLoaded = true;
    $("settings-status").textContent = isDemo() ? "sample settings · read only" : "saved on this machine";
  }

  function renderQueue() {
    const current = status.current_job;
    const running = isRunning();
    const waiting = queue();
    const paused = Boolean(status.queue_paused);
    $("queue-count").textContent = waiting.length;
    $("nav-queue-count").textContent = waiting.length;
    $("nav-queue-count").hidden = waiting.length === 0;
    stateBadge($("queue-state"), paused ? "paused" : running ? "running" : "neutral", paused ? "Paused" : running ? "Running" : "Ready");
    $("current-job").hidden = !running;
    $("current-job-title").textContent = taskName(current?.task || status.task);
    $("current-job-phase").textContent = status.phase || "Starting task";
    $("queue-empty").hidden = waiting.length > 0;
    $("queue-empty").querySelector("strong").textContent = paused ? "queue’s paused." : running ? "nothing queued after this." : "nothing queued.";
    $("queue-empty").querySelector("p").textContent = paused
      ? "add whatever you want. hit resume when you’re ready."
      : "add a task above. they run one at a time, in order.";
    const signature = JSON.stringify(waiting);
    if (signature === queueSignature) return;
    queueSignature = signature;
    const list = document.createDocumentFragment();
    waiting.forEach((job, index) => {
      const item = document.createElement("li");
      item.className = "queue-item";
      const position = document.createElement("span");
      position.className = "queue-position";
      position.textContent = index + 1;
      position.setAttribute("aria-label", `Queue position ${index + 1}`);
      const main = document.createElement("div");
      main.className = "queue-item-main";
      const title = document.createElement("strong");
      title.textContent = taskName(job.task);
      const subtitle = document.createElement("small");
      subtitle.textContent = `Added ${shortTime(job.created_at)}`;
      main.append(title, subtitle);
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "icon-button";
      cancel.dataset.cancelJob = job.id;
      cancel.setAttribute("aria-label", `Cancel queued ${taskName(job.task)} at position ${index + 1}`);
      cancel.title = "Remove from queue";
      cancel.textContent = "×";
      item.append(position, main, cancel);
      list.append(item);
    });
    $("queue-list").replaceChildren(list);
  }

  function renderActivity() {
    const logs = (Array.isArray(status.logs) ? status.logs : []).filter((log) => {
      // CLI result fields already appear in the run summary; keep the log readable.
      const message = String(log.message || "").trim();
      return message !== "{" && message !== "}" && !/^"(?:status|run_dir|duration|actions)"\s*:/.test(message);
    });
    $("log-count").textContent = logs.length ? `${logs.length} event${logs.length === 1 ? "" : "s"}` : "No events yet";
    $("logs-empty").hidden = logs.length > 0;
    const signature = JSON.stringify(logs);
    if (signature !== logSignature) {
      logSignature = signature;
      const list = document.createDocumentFragment();
      logs.slice(-100).reverse().forEach((log) => {
        const item = document.createElement("li");
        item.className = `activity-item${log.level === "error" ? " error" : ""}`;
        const dot = document.createElement("span");
        dot.className = "activity-dot";
        dot.setAttribute("aria-hidden", "true");
        const timestamp = document.createElement("time");
        timestamp.textContent = clockTime(log.time);
        if (dateValue(log.time)) timestamp.dateTime = log.time;
        const message = document.createElement("span");
        message.className = "activity-message";
        message.textContent = log.message;
        item.append(dot, timestamp, message);
        list.append(item);
      });
      $("activity-list").replaceChildren(list);
    }
    const history = Array.isArray(status.history) ? status.history : [];
    $("history-section").hidden = history.length === 0;
    const nextHistorySignature = JSON.stringify(history);
    if (nextHistorySignature === historySignature) return;
    historySignature = nextHistorySignature;
    const historyList = document.createDocumentFragment();
    [...history].sort((a, b) => (dateValue(b.completed_at)?.getTime() || 0) - (dateValue(a.completed_at)?.getTime() || 0)).slice(0, 5).forEach((job) => {
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = taskName(job.task);
      const timestamp = document.createElement("time");
      timestamp.textContent = shortTime(job.completed_at);
      if (dateValue(job.completed_at)) timestamp.dateTime = job.completed_at;
      const badge = document.createElement("span");
      stateBadge(badge, job.state, ({ disabled: "Disabled", deferred: "Awaiting reset test", success: "Completed", failed: "Failed", stopped: "Stopped" }[job.state] || job.state));
      item.append(title, timestamp, badge);
      historyList.append(item);
    });
    $("history-list").replaceChildren(historyList);
  }

  function renderFrame() {
    if (!status.has_frame) {
      frameLoaded = false;
      frameVersion = null;
      $("game-frame").hidden = true;
      $("game-frame").removeAttribute("src");
      $("frame-placeholder").hidden = false;
      $("frame-caption").textContent = "no screenshot yet";
      renderMapVisibility();
      return;
    }
    const version = String(status.frame_version ?? "latest");
    $("frame-caption").textContent = isDemo() ? "original schematic · no game screenshot" : status.app_closed ? "last screenshot · game closed after the run" : "latest screenshot";
    if (version === frameVersion) return;
    frameVersion = version;
    $("game-frame").src = `/api/frame?v=${encodeURIComponent(version)}`;
  }

  function render() {
    if (!status) return;
    $("demo-banner").hidden = !isDemo();
    $("game-frame").alt = isDemo() ? "Original schematic of home navigation targets, with fictional demo data" : "Latest screenshot from the selected Blue Archive instance";
    if (isDemo()) {
      document.title = "Demo · Maid in Schale · Blue Archive Automator";
      $("screen-title").textContent = "screen map preview";
      $("settings-lock-note").textContent = "demo settings are read only. there’s no device connected.";
    }
    const running = isRunning();
    stateBadge($("state-badge"), status.state, ({ disabled: "Disabled", deferred: "Awaiting reset test", idle: "Ready", running: "Running", success: status.task === "restart" && !status.app_closed ? "Home reached" : "Completed", failed: "Needs attention", stopped: "Stopped" }[status.state] || status.state));
    $("run-phase").textContent = status.phase || "close the game, open it again, get to home. deal with the popups on the way.";
    $("current-task").textContent = running ? taskName(status.current_job?.task || status.task) : "None running";
    $("device-serial").textContent = status.config?.serial || "—";
    $("sidebar-serial").textContent = status.config?.serial || "Not configured";
    renderElapsed();
    renderSettings(status.config);
    renderQueue();
    renderActivity();
    renderFrame();
    renderControls();
  }

  async function readResponse(response) {
    let body;
    try { body = await response.json(); }
    catch { throw new Error("The local server returned an unreadable response."); }
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status}).`);
    return body;
  }

  async function refreshStatus() {
    if (refreshPromise) return refreshPromise;
    refreshPromise = (async () => {
      try {
        const response = await fetch("/api/status", { cache: "no-store", signal: AbortSignal.timeout(10000) });
        status = await readResponse(response);
        connected = true;
        $("connection").className = "connection connected";
        $("connection-text").textContent = isDemo() ? "local demo · no device" : "local server connected";
        render();
        if (!mapData && !mapLoading) void loadMap();
        if (!popupsLoading) void loadPopups();
        if (!actionsLoading) void loadActions();
        return status;
      } catch (error) {
        connected = false;
        $("connection").className = "connection disconnected";
        $("connection-text").textContent = "can’t reach the local server";
        $("run-phase").textContent = status
          ? "lost the connection. trying the local server again."
          : "waiting for the local server. this page will reconnect on its own.";
        stateBadge($("state-badge"), "neutral", "Offline");
        renderControls();
        return null;
      } finally { refreshPromise = null; }
    })();
    return refreshPromise;
  }

  function schedulePoll() {
    clearTimeout(polling);
    polling = setTimeout(async () => {
      // Wait out an older poll before requesting the state after this mutation.
      if (refreshPromise) await refreshPromise;
      await refreshStatus();
      schedulePoll();
    }, isRunning() ? 1000 : 3000);
  }

  async function post(path, body, successMessage) {
    if (isDemo()) {
      showError("this is the demo. controls are read only, and no device is connected.");
      return null;
    }
    pending += 1;
    renderControls();
    try {
      if (!status?.csrf_token) await refreshStatus();
      if (!connected || !status?.csrf_token) throw new Error("The local server is unavailable. Wait for it to reconnect and try again.");
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": status.csrf_token },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(path === "/api/capture" ? 45000 : 15000),
      });
      const result = await readResponse(response);
      $("error-banner").hidden = true;
      if (successMessage) showNotice(successMessage);
      await refreshStatus();
      return result;
    } catch (error) {
      const timeout = error.name === "TimeoutError" || error.name === "AbortError";
      showError(timeout ? "The request took too long to return. Check the queue and activity before trying again." : error.message);
      await refreshStatus();
      return null;
    } finally {
      pending -= 1;
      renderControls();
      schedulePoll();
    }
  }

  function svgElement(name, attributes) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }

  function selectMapButton(button) {
    selectedButton = button.id;
    document.querySelectorAll(".map-hit").forEach((element) => {
      const selected = element.dataset.buttonId === button.id;
      element.classList.toggle("selected", selected);
      element.setAttribute("aria-pressed", String(selected));
    });
    $("map-detail-title").textContent = button.label;
    $("map-detail-description").textContent = button.description || "Home screen navigation target.";
    stateBadge($("map-detail-status"), button.verified ? "success" : "neutral", button.verified ? "Verified" : "Mapped");
    const labels = new Map(mapData.buttons.map((entry) => [entry.id, entry.label]));
    labels.set("home", "Home");
    const route = Array.isArray(button.route) ? button.route : [];
    $("map-detail-route").textContent = route.length ? route.map((id) => labels.get(id) || id).join(" → ") : "Home → " + button.label;
    $("map-detail-position").textContent = button.center.join(", ") + " px";
    $("map-detail-kind").textContent = button.kind === "carousel" ? "Rotating banner" : "Fixed button";
    $("map-detail").hidden = false;
  }

  function renderMapVisibility() {
    const visible = $("map-toggle").checked && frameLoaded && Boolean(mapData);
    // SVG does not reflect HTMLElement.hidden, so update the attribute itself.
    $("map-overlay").toggleAttribute("hidden", !visible);
    $("map-hint").hidden = !visible;
    $("map-detail").hidden = !visible || !selectedButton;
  }

  function buildMap() {
    const overlay = $("map-overlay");
    overlay.setAttribute("viewBox", `0 0 ${mapData.width} ${mapData.height}`);
    const contents = document.createDocumentFragment();
    mapData.buttons.forEach((button) => {
      if (!Array.isArray(button.bounds) || button.bounds.length !== 4 || !Array.isArray(button.center)) return;
      const [x1, y1, x2, y2] = button.bounds;
      const width = Math.max(1, x2 - x1);
      const height = Math.max(1, y2 - y1);
      const group = svgElement("g", { class: `map-hit${button.kind === "carousel" ? " carousel" : ""}`, tabindex: 0, role: "button", "aria-label": `Inspect ${button.label}`, "aria-pressed": "false" });
      group.dataset.buttonId = button.id;
      const bounds = svgElement("rect", { class: "map-bounds", x: x1, y: y1, width, height, rx: 6 });
      const labelWidth = Math.min(260, Math.max(60, String(button.label).length * 7.6 + 18));
      const labelX = Math.max(2, Math.min(x1, mapData.width - labelWidth - 2));
      const labelY = Math.max(2, Math.min(y1 - 23, mapData.height - 24));
      const labelBackground = svgElement("rect", { class: "map-label-bg", x: labelX, y: labelY, width: labelWidth, height: 22, rx: 4 });
      const label = svgElement("text", { x: labelX + 8, y: labelY + 15 });
      label.textContent = button.label;
      const center = svgElement("circle", { class: "map-center", cx: button.center[0], cy: button.center[1], r: 4 });
      group.append(bounds, center, labelBackground, label);
      group.addEventListener("click", () => selectMapButton(button));
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectMapButton(button); }
      });
      contents.append(group);
    });
    overlay.replaceChildren(contents);
    renderControls();
    renderMapVisibility();
  }

  async function loadMap() {
    mapLoading = true;
    try {
      const response = await fetch("/api/map", { cache: "no-store", signal: AbortSignal.timeout(10000) });
      const data = await readResponse(response);
      if (data.width !== 1280 || data.height !== 720 || !Array.isArray(data.buttons)) throw new Error("The home map has an unsupported format.");
      mapData = data;
      buildMap();
    } catch {
      $("map-toggle").disabled = true;
      $("map-toggle").title = "Home button map is not available yet";
    } finally { mapLoading = false; }
  }

  function popupImage(url, label) {
    const container = document.createElement("div");
    container.className = "popup-image";
    const caption = document.createElement("span");
    caption.textContent = label;
    caption.className = "popup-image-label";
    let safeUrl = null;
    try {
      const resolved = new URL(url, window.location.href);
      if (url && resolved.origin === window.location.origin && ["http:", "https:"].includes(resolved.protocol)) safeUrl = resolved.href;
    } catch { /* Missing or malformed screenshots stay visibly unavailable. */ }
    if (safeUrl) {
      const link = document.createElement("a");
      link.href = safeUrl;
      link.target = "_blank";
      link.rel = "noopener";
      link.setAttribute("aria-label", `Open ${label.toLowerCase()} popup screenshot at full size`);
      const image = document.createElement("img");
      image.src = safeUrl;
      image.alt = `${label} the popup close attempt`;
      image.loading = "lazy";
      link.append(image, caption);
      container.append(link);
    } else {
      const placeholder = document.createElement("span");
      placeholder.className = "popup-image-pending";
      placeholder.textContent = label === "After" ? "Awaiting next frame" : "Frame unavailable";
      container.append(placeholder, caption);
    }
    return container;
  }

  function renderPopups() {
    $("popups-empty").hidden = popups.length > 0;
    $("popup-count").textContent = popups.length ? `${popups.length} close attempt${popups.length === 1 ? "" : "s"}` : "No attempts yet";
    $("popup-more-row").hidden = popups.length <= popupLimit;
    const list = document.createDocumentFragment();
    [...popups].sort((a, b) => (dateValue(b.time)?.getTime() || 0) - (dateValue(a.time)?.getTime() || 0)).slice(0, popupLimit).forEach((popup) => {
      const card = document.createElement("article");
      card.className = "popup-entry";
      const images = document.createElement("div");
      images.className = "popup-images";
      images.append(popupImage(popup.before_url, "Before"), popupImage(popup.after_url, "After"));
      const info = document.createElement("div");
      info.className = "popup-info";
      const heading = document.createElement("div");
      heading.className = "popup-heading";
      const title = document.createElement("h3");
      title.textContent = "close attempted";
      const badge = document.createElement("span");
      const result = popup.result || "pending";
      stateBadge(badge, result === "changed" ? "running" : result === "unchanged" ? "stopped" : "neutral", result === "changed" ? "screen changed" : result === "unchanged" ? "still visible" : "waiting for the next frame");
      heading.append(title, badge);
      const reason = document.createElement("p");
      reason.className = "popup-reason";
      const reasonLabel = document.createElement("strong");
      reasonLabel.textContent = "Detection: ";
      reason.append(reasonLabel, document.createTextNode(popup.detail || "Recognized popup close control"));
      const meta = document.createElement("div");
      meta.className = "popup-meta";
      const detector = document.createElement("code");
      detector.textContent = popup.detector || "popup detector";
      const timestamp = document.createElement("time");
      timestamp.textContent = clockTime(popup.time);
      if (dateValue(popup.time)) timestamp.dateTime = popup.time;
      const job = document.createElement("span");
      job.textContent = `Job ${String(popup.job_id || "—").slice(0, 8)}`;
      job.title = String(popup.job_id || "No job identifier");
      meta.append(detector, timestamp, job);
      if (popup.after_state) {
        const after = document.createElement("span");
        after.textContent = `Next screen: ${popup.after_state}`;
        meta.append(after);
      }
      info.append(heading, reason, meta);
      card.append(images, info);
      list.append(card);
    });
    $("popup-list").replaceChildren(list);
  }

  async function loadPopups() {
    popupsLoading = true;
    try {
      const response = await fetch("/api/popups", { cache: "no-store", signal: AbortSignal.timeout(10000) });
      const data = await readResponse(response);
      if (!Array.isArray(data.popups)) throw new Error("Popup log response is invalid.");
      const signature = JSON.stringify(data.popups);
      if (signature !== popupSignature) {
        popupSignature = signature;
        popups = data.popups;
        renderPopups();
      }
    } catch {
      if (!popups.length) $("popup-count").textContent = "Log unavailable";
    } finally { popupsLoading = false; }
  }

  function renderActions() {
    $("actions-empty").hidden = importantActions.length > 0;
    $("actions-table-wrap").hidden = importantActions.length === 0;
    $("actions-count").textContent = importantActions.length ? `${importantActions.length} saved action${importantActions.length === 1 ? "" : "s"}` : "No actions yet";
    $("actions-more-row").hidden = importantActions.length <= actionsLimit;
    const rows = document.createDocumentFragment();
    [...importantActions].sort((a, b) => (dateValue(b.time)?.getTime() || 0) - (dateValue(a.time)?.getTime() || 0)).slice(0, actionsLimit).forEach((action) => {
      const row = document.createElement("tr");
      const time = dateValue(action.time);
      const when = time ? `${time.toLocaleDateString([], { month: "short", day: "numeric" })} · ${shortTime(action.time)}` : "—";
      const name = String(action.action || "Action").replaceAll("_", " ");
      const place = [action.student, action.cafe ? `Café ${action.cafe}` : null, action.location, action.room].filter(Boolean).join(" · ") || "—";
      [when, taskName(action.task), name, action.detail || "—", place].forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      });
      rows.append(row);
    });
    $("actions-list").replaceChildren(rows);
  }

  async function loadActions() {
    actionsLoading = true;
    try {
      const response = await fetch("/api/actions", { cache: "no-store", signal: AbortSignal.timeout(10000) });
      const data = await readResponse(response);
      if (!Array.isArray(data.actions)) throw new Error("Action history response is invalid.");
      const signature = JSON.stringify(data.actions);
      if (signature !== actionsSignature) {
        actionsSignature = signature;
        importantActions = data.actions;
        renderActions();
      }
    } catch {
      if (!importantActions.length) $("actions-count").textContent = "History unavailable";
    } finally { actionsLoading = false; }
  }

  $("run-restart").addEventListener("click", () => void post("/api/run", { task: "restart" }, "restart’s in the queue."));
  $("run-daily").addEventListener("click", () => void post("/api/run", { task: "daily" }, "daily’s in the queue."));
  $("run-club").addEventListener("click", () => void post("/api/run", { task: "club" }, "club placeholder queued. live testing comes after reset."));
  $("run-cafe").addEventListener("click", () => void post("/api/run", { task: "cafe" }, "café’s in the queue."));
  $("run-crafting").addEventListener("click", () => void post("/api/run", { task: "crafting" }, "crafting’s in the queue."));
  $("run-lessons").addEventListener("click", () => void post("/api/run", { task: "lessons" }, "lessons are in the queue."));
  $("stop-run").addEventListener("click", () => void post("/api/stop", {}, "stopping this task and pausing the queue. everything waiting stays there."));
  $("pause-queue").addEventListener("click", () => void post("/api/pause", {}, "queue paused. the current task can finish."));
  $("resume-queue").addEventListener("click", () => void post("/api/resume", {}, "queue’s running again."));
  $("capture").addEventListener("click", () => void post("/api/capture", {}));
  $("popup-show-more").addEventListener("click", () => { popupLimit += 6; renderPopups(); });
  $("actions-show-more").addEventListener("click", () => { actionsLimit += 15; renderActions(); });
  $("dismiss-error").addEventListener("click", () => { $("error-banner").hidden = true; });
  $("queue-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-cancel-job]");
    if (button) void post("/api/cancel", { id: button.dataset.cancelJob }, "removed from the queue.");
  });
  $("settings-form").addEventListener("input", () => {
    settingsDirty = true;
    fields.cafe_invite_student.setCustomValidity("");
    $("settings-status").textContent = "not saved yet";
    renderControls();
  });
  $("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (fields.cafe_invite_enabled.checked && !fields.cafe_invite_student.value.trim()) {
      fields.cafe_invite_student.setCustomValidity("Enter the student’s exact English name to enable invitations.");
    }
    if (!$("settings-form").reportValidity()) return;
    const values = Object.fromEntries(settingNames.map((name) => {
      const value = booleanSettings.has(name) ? fields[name].checked
        : name === "lessons_locations" ? fields[name].value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
        : stringSettings.has(name) ? fields[name].value.trim() : Number(fields[name].value);
      return [name, value];
    }));
    const result = await post("/api/settings", values, "settings saved.");
    if (result !== null) {
      settingsDirty = false;
      renderSettings(status?.config);
      renderControls();
    }
  });
  $("map-toggle").addEventListener("change", renderMapVisibility);
  $("close-map-detail").addEventListener("click", () => {
    const previous = selectedButton;
    selectedButton = null;
    document.querySelectorAll(".map-hit").forEach((element) => {
      element.classList.remove("selected");
      element.setAttribute("aria-pressed", "false");
      if (element.dataset.buttonId === previous) element.focus();
    });
    renderMapVisibility();
  });
  $("game-frame").addEventListener("load", () => {
    frameLoaded = true;
    $("game-frame").hidden = false;
    $("frame-placeholder").hidden = true;
    renderControls();
    renderMapVisibility();
  });
  $("game-frame").addEventListener("error", () => {
    frameLoaded = false;
    $("game-frame").hidden = true;
    $("frame-placeholder").hidden = false;
    $("frame-placeholder-title").textContent = "the screenshot didn’t load.";
    $("frame-placeholder-text").textContent = "try refreshing the screen.";
    $("frame-caption").textContent = "screenshot unavailable";
    renderControls();
    renderMapVisibility();
  });
  document.querySelectorAll(".nav-link").forEach((link) => link.addEventListener("click", () => {
    document.querySelectorAll(".nav-link").forEach((item) => item.classList.toggle("active", item === link));
  }));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void refreshStatus().then(schedulePoll);
  });
  setInterval(renderElapsed, 1000);
  void refreshStatus().then(schedulePoll);
})();
