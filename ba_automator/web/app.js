"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const SVG_NS = "http://www.w3.org/2000/svg";
  const booleanSettings = new Set(["checkin_schedule_enabled", "daily_schedule_enabled", "total_assault_enabled_in_daily", "bounties_enabled_in_daily", "scrimmages_enabled_in_daily", "packs_monthly_enabled", "packs_half_monthly_enabled", "packs_ap_enabled", "auto_download", "crafting_schedule_enabled", "cafe_schedule_enabled", "cafe_invite_enabled", "close_app_when_idle", "lessons_enabled_in_daily"]);
  const stringSettings = new Set(["total_assault_difficulty", "cafe_invite_student", "lessons_strategy"]);
  const settingNames = ["checkin_schedule_enabled", "checkin_interval_minutes", "daily_schedule_enabled", "daily_reset_delay_minutes", "total_assault_enabled_in_daily", "total_assault_difficulty", "total_assault_comfort_seconds", "bounties_enabled_in_daily", "scrimmages_enabled_in_daily", "ap_floor","packs_monthly_enabled", "packs_half_monthly_enabled", "packs_ap_enabled", "packs_monthly_max_cents", "packs_half_monthly_max_cents", "packs_ap_max_cents", "auto_download", "poll_interval", "startup_timeout", "download_timeout", "unknown_timeout", "crafting_schedule_enabled", "close_app_when_idle", "cafe_schedule_enabled", "cafe_invite_enabled", "cafe_invite_student", "lessons_strategy", "lessons_max_tickets", "lessons_locations", "lessons_enabled_in_daily"];
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
  let lootLoading = false;
  let lootSignature = "";
  let importantActions = [];
  let actionsLimit = 15;
  let noticeTimer = null;

  const taskName = (task) => ({ total_assault: "Total Assault", assault_rewards: "Total Assault rewards", tactical_rewards: "Tactical rewards", red_dots: "Collect red dots", free_pack: "Free pack + mail", tasks: "Collect Tasks", bounties: "Bounties", scrimmages: "Scrimmages", spend_ap: "Spend AP", scan_ap: "Scan stages", packs: "Packs + mail", mail: "Collect mail", restart: "Restart", daily: "Daily", club: "Club", crafting: "Crafting", cafe: "Café", lessons: "Lessons" }[task] || task || "—");
  const jobName = (job) => job?.source === "checkin" && job?.task === "red_dots" ? "Check-in" : taskName(job?.task);
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

  function renderDailySchedule() {
    const schedule = status?.schedule?.daily;
    const enabled = schedule?.enabled ?? status?.config?.daily_schedule_enabled ?? false;
    const summary = $("daily-schedule-status");
    $("daily-schedule-title").textContent = enabled ? "daily routine" : "daily schedule";
    const queued = queue().some((job) => job.task === "daily");
    const running = status?.current_job?.task === "daily";
    const next = dateValue(schedule?.next_due_at);
    const nextLabel = next?.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const currentOccurrence = Boolean(schedule?.game_day) && schedule.game_day === schedule.current_game_day;
    const notes = [];
    if (running) notes.push("running now");
    else if (queued) notes.push(status?.queue_paused ? "queued · waiting for resume" : "queued · runs in order");
    else if (schedule?.blocked_reason) notes.push(`needs a look: ${schedule.blocked_reason}`);
    else if (["failed", "stopped"].includes(schedule?.status)) notes.push(currentOccurrence ? "didn't finish. check the log, then queue daily to retry." : "last daily didn't finish");
    else if (schedule?.status === "success") notes.push(currentOccurrence ? "done for this game day" : "last daily finished");
    else if (schedule?.status === "skipped") notes.push(currentOccurrence ? "skipped for this game day" : "last scheduled daily was skipped");
    if (!enabled) notes.push("schedule off · queue daily for a single run");
    else if (next && next.getTime() <= Date.now()) {
      if (!queued && !running) notes.push(status?.queue_paused ? "due now · queue is paused" : "due now · goes in the queue");
    } else if (nextLabel) notes.push(`next: ${nextLabel}`);
    else if (!queued && !running && !schedule?.blocked_reason) notes.push("waiting for the next game reset");
    summary.textContent = notes.join(" · ");
  }

  function renderCheckinSchedule() {
    const schedule = status?.schedule?.checkin;
    const enabled = schedule?.enabled ?? status?.config?.checkin_schedule_enabled ?? false;
    const interval = schedule?.interval_minutes ?? status?.config?.checkin_interval_minutes ?? 30;
    const isCheckin = (job) => job?.task === "red_dots" && job?.source === "checkin";
    const running = isCheckin(status?.current_job);
    const queued = queue().some(isCheckin);
    const next = dateValue(schedule?.next_due_at);
    const last = dateValue(schedule?.last_success_at);
    const notes = [];
    $("checkin-schedule-title").textContent = enabled ? "next check-in" : "check-in schedule";
    if (running) notes.push("checking red dots and AP now");
    else if (queued) notes.push(status?.queue_paused ? "queued · waiting for resume" : "queued · runs in order");
    else if (schedule?.blocked_reason) notes.push(`needs a look: ${schedule.blocked_reason}`);
    else if (["failed", "stopped"].includes(schedule?.status)) notes.push("last check didn't finish · see the log");
    else if (last) notes.push(`last checked ${last.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`);
    if (!enabled) notes.push("schedule off");
    else if (!running && !queued) {
      if (next && next.getTime() <= Date.now()) notes.push(status?.queue_paused ? "due now · queue is paused" : "due now · goes in the queue");
      else if (next) notes.push(`next: ${next.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}${status?.queue_paused ? " · queue is paused" : ""}`);
      else notes.push(status?.queue_paused ? "waiting for resume" : `every ${interval} minutes · waiting for the next check`);
    }
    $("checkin-schedule-status").textContent = notes.join(" · ");
  }

  function renderSchedule() {
    renderDailySchedule();
    renderCheckinSchedule();
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
    $("run-available").disabled = unavailable || status?.run_available_pending === undefined
      || (Boolean(status.run_available_pending || status.run_available_active) && !paused);
    $("run-restart").disabled = unavailable;
    $("clear-loot").disabled = unavailable;
    $("run-daily").disabled = unavailable;
    $("run-cafe").disabled = unavailable;
    $("run-club").disabled = unavailable;
    $("run-crafting").disabled = unavailable;
    $("run-mail").disabled = unavailable;
    $("run-tasks").disabled = unavailable;
    $("run-red-dots").disabled = unavailable;
    $("run-tactical").disabled = unavailable;
    $("run-total-assault").disabled = unavailable;
    $("run-free-pack").disabled = unavailable;
    $("run-ap").disabled = unavailable;
    $("run-packs").disabled = unavailable;
    $("run-bounties").disabled = unavailable;
    $("run-scrimmages").disabled = unavailable;
    $("run-lessons").disabled = unavailable;
    $("stop-run").disabled = unavailable || !active;
    $("capture").disabled = unavailable || active;
    $("pause-queue").disabled = unavailable || paused;
    $("pause-queue").textContent = paused
      ? active ? "pausing after this job" : "paused"
      : active ? "pause after this job" : "pause queue";
    $("queue-control-note").textContent = paused
      ? active ? "the current job is still finishing. no next job will start. do everything gets the queue moving again."
        : "queue's paused. do everything checks what's ready and gets things moving again."
      : status?.run_available_pending ? "finishing what's already queued, then checking what else is ready."
      : status?.run_available_active ? "getting through what's ready. everything runs one at a time."
      : "run the enabled jobs that are due, then check red dots and AP. your settings and AP floor still apply.";
    $("resume-queue").hidden = !paused;
    $("resume-queue").disabled = unavailable;
    $("settings-fields").disabled = unavailable || active || queued;
    $("settings-lock-note").hidden = !active && !queued;
    $("save-settings").disabled = unavailable || active || queued || !settingsDirty;
    fields.checkin_interval_minutes.disabled = !fields.checkin_schedule_enabled.checked;
    fields.cafe_invite_student.disabled = unavailable || !fields.cafe_invite_enabled.checked;
    $("lessons-strategy-description").textContent = fields.lessons_strategy.value === "school_rank"
      ? "lowest rank first, then lowest XP. recheck after each ticket. pick the room with the most students; higher owned relationships break ties."
      : "check every location first. use tickets on rooms with the most owned students; higher relationships break ties.";
    const packEnabled = ["monthly", "half_monthly", "ap"].some((key) => status?.config?.[`packs_${key}_enabled`]);
    $("daily-plan").textContent = `daily does restart → club → free pack → ${packEnabled ? "packs → " : ""}mail → café${status?.config?.bounties_enabled_in_daily === false ? "" : " → bounties"}${status?.config?.scrimmages_enabled_in_daily === false ? "" : " → scrimmages"} → tactical rewards${status?.config?.lessons_enabled_in_daily === false ? "" : " → lessons"}${status?.config?.total_assault_enabled_in_daily ? " → total assault" : ""} → raid rewards${status?.config?.ap_schedule_enabled ? " → spend AP" : ""} → collect tasks. home and campaign red dots queue their collection jobs when a run finishes.`;
    const packs = status?.schedule?.packs;
    $("packs-status").textContent = packs?.blocked_reason
      ? `paused: ${packs.blocked_reason}. check the game, then queue a pack check.`
      : packs?.pending ? "a purchase needs checking. another charge won’t be attempted."
      : packEnabled ? `renewals enabled. next check: ${packs?.next_check_at ? shortTime(packs.next_check_at) : "when the queue is ready"}.`
      : "paid renewals are off. check packs + mail will only inspect ownership and collect mail.";
    $("map-toggle").disabled = !frameLoaded || !mapData;
    document.querySelectorAll("[data-cancel-job]").forEach((button) => { button.disabled = unavailable; });
  }

  function renderSettings(config) {
    if (!config || (settingsLoaded && settingsDirty)) return;
    settingNames.forEach((name) => {
      if (booleanSettings.has(name)) fields[name].checked = Boolean(config[name] ?? (["checkin_schedule_enabled", "lessons_enabled_in_daily", "bounties_enabled_in_daily", "scrimmages_enabled_in_daily"].includes(name)));
      else if (name.endsWith("_max_cents")) fields[name].value = ((config[name] ?? (name.includes("half_monthly") || name.includes("_ap_") ? 299 : 699)) / 100).toFixed(2);
      else if (name === "lessons_locations") fields[name].value = Array.isArray(config[name]) ? config[name].join("\n") : "";
      else fields[name].value = config[name] ?? ({ checkin_interval_minutes: 30, daily_reset_delay_minutes: 1, total_assault_difficulty: "hardcore", total_assault_comfort_seconds: 30, ap_floor: 100, lessons_strategy: "relationship", lessons_max_tickets: 0 }[name] ?? "");
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
    stateBadge($("queue-state"), running ? "running" : paused ? "paused" : "neutral",
      paused ? running ? "Pausing after this job" : "Paused" : running ? "Running" : "Ready");
    $("current-job").hidden = !running;
    $("current-job-title").textContent = current ? jobName(current) : taskName(status.task);
    $("current-job-phase").textContent = status.phase || "Starting task";
    $("queue-empty").hidden = waiting.length > 0;
    $("queue-empty").querySelector("strong").textContent = paused
      ? running ? "pausing after this job." : "queue’s paused."
      : running ? "nothing queued after this." : "nothing queued.";
    $("queue-empty").querySelector("p").textContent = paused
      ? running ? "the current job is still finishing. the queue will wait after that."
        : "hit do everything when you’re ready to get going again."
      : "hit do everything, or let the next scheduled check handle it.";
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
      title.textContent = jobName(job);
      const subtitle = document.createElement("small");
      subtitle.textContent = `Added ${shortTime(job.created_at)}`;
      main.append(title, subtitle);
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "icon-button";
      cancel.dataset.cancelJob = job.id;
      cancel.setAttribute("aria-label", `Cancel queued ${jobName(job)} at position ${index + 1}`);
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
      title.textContent = jobName(job);
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

  function renderFailures() {
    const failures = status?.failed_jobs || [];
    $("failed-jobs-card").hidden = failures.length === 0;
    const list = document.createDocumentFragment();
    failures.forEach((failure) => {
      const row = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = `${taskName(failure.task)} · ${shortTime(failure.time)}`;
      const detail = document.createElement("p"); detail.textContent = failure.detail;
      const dismiss = document.createElement("button");
      dismiss.type = "button"; dismiss.className = "button secondary small";
      dismiss.textContent = "dismiss notice";
      dismiss.disabled = !connected || pending > 0 || isDemo();
      dismiss.addEventListener("click", () => void post("/api/dismiss-failure", { id: failure.id }, "notice dismissed. retry settings haven’t changed."));
      row.append(title, detail, dismiss); list.append(row);
    });
    $("failed-jobs-list").replaceChildren(list);
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
    $("current-task").textContent = running ? (status.current_job ? jobName(status.current_job) : taskName(status.task)) : "None running";
    $("device-serial").textContent = status.config?.serial || "—";
    $("sidebar-serial").textContent = status.config?.serial || "Not configured";
    renderElapsed();
    renderSettings(status.config);
    renderQueue();
    renderFailures();
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
        if (!lootLoading) void loadLoot();
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

  async function loadLoot() {
    lootLoading = true;
    try {
      const response = await fetch("/api/loot", { cache: "no-store", signal: AbortSignal.timeout(10000) });
      const data = await readResponse(response);
      if (!Array.isArray(data.items) || !Array.isArray(data.receipts)) throw new Error("Invalid loot response");
      const signature = JSON.stringify(data);
      if (signature === lootSignature) return;
      lootSignature = signature;
      $("loot-since").textContent = data.cleared_at ? `since ${new Date(data.cleared_at).toLocaleString()}` : "all saved rewards · clear whenever you want a fresh count";
      const relationships = Array.isArray(data.relationships) ? data.relationships : [];
      $("loot-empty").hidden = data.receipt_count > 0 || relationships.length > 0;
      const countParts = data.receipt_count ? [`${data.items.length.toLocaleString()} ${data.items.length === 1 ? "kind of loot" : "kinds of loot"}`, `${data.receipt_count.toLocaleString()} ${data.receipt_count === 1 ? "receipt" : "receipts"}`] : [];
      if (relationships.length) countParts.push(`${relationships.length.toLocaleString()} relationship ${relationships.length === 1 ? "level-up" : "level-ups"}`);
      $("loot-count").textContent = countParts.join(" · ");
      const totals = document.createDocumentFragment();
      const groups = Array.isArray(data.groups) ? [...data.groups] : [{ id: "other", label: "the haul", items: data.items }];
      const icons = new Map(groups.flatMap((group) => group.items.map((item) => [item.name, item.icon_url])));
      const makeIcon = (item) => {
        const visual = document.createElement("div"); visual.className = "loot-icon"; visual.setAttribute("aria-hidden", "true");
        const missingIcon = () => {
          visual.replaceChildren(); visual.classList.add("loot-icon-missing"); visual.textContent = "?"; visual.title = "no saved game icon yet";
        };
        const url = item.icon_url || icons.get(item.name);
        if (/^\/api\/loot\/icons\/[a-f0-9]{64}$/.test(url || "")) {
          const img = document.createElement("img"); img.src = url; img.alt = ""; img.loading = "lazy"; img.width = 72; img.height = 72;
          img.addEventListener("error", missingIcon, { once: true });
          visual.append(img);
        } else missingIcon();
        return visual;
      };
      const quantityText = (quantity) => Number.isSafeInteger(quantity) && quantity > 0 ? `+${quantity.toLocaleString()}` : "?";
      const makeItem = (item, unresolved = false) => {
        const card = document.createElement("article"); card.className = "loot-item";
        const quantity = document.createElement("strong"); quantity.textContent = quantityText(item.quantity);
        const label = document.createElement("span"); label.textContent = item.name; label.title = item.name;
        if (!unresolved) {
          card.classList.add("loot-item-tile");
          label.className = "loot-item-name";
          card.append(label, makeIcon(item), quantity);
          return card;
        }
        const text = document.createElement("div"); text.className = "loot-item-text";
        text.append(quantity, label); card.append(makeIcon(item), text);
        if (/^\/api\/loot\/[a-f0-9]{32}\/receipt$/.test(item.receipt_url || "")) {
          const link = document.createElement("a"); link.href = item.receipt_url; link.target = "_blank"; link.rel = "noopener"; link.textContent = "check receipt";
          text.append(link);
        }
        return card;
      };
      if (data.unresolved_items?.length) groups.push({ id: "unresolved", label: "needs a closer look", items: data.unresolved_items });
      const openGroups = new Set([...document.querySelectorAll('details[data-loot-group][open]')].map((section) => section.dataset.lootGroup));
      let relationshipsAdded = false;
      const appendRelationships = () => {
        if (relationshipsAdded || !relationships.length) return;
        relationshipsAdded = true;
        const section = document.createElement("section"); section.className = "loot-group loot-relationships"; section.dataset.lootGroup = "relationships";
        const heading = document.createElement("h3"); heading.textContent = "relationship gains";
        const count = document.createElement("span"); count.textContent = `${relationships.length} ${relationships.length === 1 ? "level-up" : "level-ups"}`; heading.append(count);
        const header = document.createElement("div"); header.className = "loot-relationship-intro";
        const artLink = document.createElement("a"); artLink.className = "mascot-art-link";
        artLink.href = "/maid-arisu-heart.png"; artLink.target = "_blank"; artLink.rel = "noopener";
        artLink.setAttribute("aria-label", "View Maid Arisu hugging a heart at full size (opens in a new tab)");
        artLink.title = "view full-size art";
        const art = document.createElement("img"); art.src = artLink.href; art.alt = "";
        art.className = "arisu-heart"; art.width = 80; art.height = 80; art.loading = "lazy";
        artLink.append(art); header.append(heading, artLink);
        const grid = document.createElement("div"); grid.className = "loot-relationship-grid";
        relationships.forEach((gain) => {
          const card = document.createElement("article"); card.className = "loot-relationship";
          const title = document.createElement("div"); title.className = "loot-relationship-heading";
          const student = document.createElement("strong"); student.textContent = gain.student || "relationship increased";
          const rank = document.createElement("span"); rank.className = "loot-relationship-rank";
          rank.textContent = Number.isSafeInteger(gain.rank) && gain.rank > 0 ? `Lv. ${gain.rank}` : "level unread";
          title.append(student, rank); card.append(title);
          const time = document.createElement("time"); const when = dateValue(gain.time);
          time.textContent = `${taskName(gain.task)} · ${when ? when.toLocaleString() : "time unavailable"}`;
          if (when) time.dateTime = when.toISOString();
          card.append(time);
          const stats = document.createElement("dl"); stats.className = "loot-relationship-stats";
          (gain.stats || []).forEach((stat) => {
            const name = document.createElement("dt"); name.textContent = stat.name;
            const value = document.createElement("dd");
            const before = Number.isSafeInteger(stat.before), after = Number.isSafeInteger(stat.after), delta = Number.isSafeInteger(stat.delta);
            const change = delta ? `${stat.delta >= 0 ? "+" : ""}${stat.delta.toLocaleString()}` : "";
            value.textContent = before && after ? `${stat.before.toLocaleString()} → ${stat.after.toLocaleString()}${delta ? ` (${change})` : ""}`
              : after ? `now ${stat.after.toLocaleString()}${delta ? ` (${change})` : ""}`
              : delta ? change : before ? `was ${stat.before.toLocaleString()} · new value unread` : "unread";
            stats.append(name, value);
          });
          if (stats.childElementCount) card.append(stats);
          if (!gain.student || !gain.rank || !gain.stats?.length || gain.incomplete) {
            const note = document.createElement("p"); note.className = "loot-relationship-note";
            const missing = [];
            if (!gain.student) missing.push("student not identified");
            if (!gain.rank) missing.push("new level unread");
            if (!gain.stats?.length) missing.push("stat changes unread");
            note.textContent = missing.length ? `${missing.join(" · ")}. known details are saved.` : "some details need a look. known changes are saved.";
            card.append(note);
          }
          if (/^\/api\/loot\/[a-f0-9]{32}\/receipt$/.test(gain.receipt_url || "")) {
            const link = document.createElement("a"); link.href = gain.receipt_url; link.target = "_blank"; link.rel = "noopener"; link.textContent = "view level-up ↗";
            card.append(link);
          }
          grid.append(card);
        });
        section.append(header, grid); totals.append(section);
      };
      groups.forEach((group) => {
        if (!["premium", "students"].includes(group.id)) appendRelationships();
        const unresolved = group.id === "unresolved";
        const collapsible = ["equipment", "other", "unresolved"].includes(group.id);
        const section = document.createElement(collapsible ? "details" : "section"); section.className = "loot-group"; section.dataset.lootGroup = group.id;
        section.classList.toggle("loot-group-highlight", ["premium", "students", "energy"].includes(group.id) && group.items.length <= 3);
        if (collapsible) section.open = openGroups.has(group.id);
        const heading = document.createElement(collapsible ? "summary" : "h3"); heading.textContent = group.label;
        const count = document.createElement("span");
        const unit = unresolved ? (group.items.length === 1 ? "entry" : "entries") : (group.items.length === 1 ? "item" : "items");
        count.textContent = `${group.items.length} ${unit}`; heading.append(count);
        const grid = document.createElement("div"); grid.className = unresolved ? "loot-grid" : "loot-grid loot-grid-tiles";
        group.items.forEach((item) => grid.append(makeItem(item, unresolved)));
        section.append(heading, grid); totals.append(section);
      });
      appendRelationships();
      $("loot-totals").replaceChildren(totals);
      $("loot-unidentified").hidden = !data.unidentified_receipts;
      const reviewReasons = [
        ["unidentified_items", "with unread names or amounts"],
        ["unverified_coverage", "with an unverified full list"],
        ["missing_details", "with no item details"],
      ];
      const reviewParts = reviewReasons.flatMap(([key, label]) => {
        const count = data.review_counts?.[key];
        return Number.isSafeInteger(count) && count > 0 ? [`${count} ${label}`] : [];
      });
      const reviewSummary = reviewParts.length ? reviewParts.join(" · ") : "we couldn't verify every item";
      $("loot-unidentified").textContent = `${data.unidentified_receipts} ${data.unidentified_receipts === 1 ? "receipt needs" : "receipts need"} a look: ${reviewSummary}. confirmed amounts are counted.`;
      $("loot-receipts-wrap").hidden = !data.receipt_count;
      $("loot-receipts-label").textContent = `${data.receipt_count} reward ${data.receipt_count === 1 ? "receipt" : "receipts"}${data.older_receipts ? " · showing the latest 100" : ""}`;
      const rows = document.createDocumentFragment();
      data.receipts.forEach((receipt) => {
        const row = document.createElement("article"); row.className = "loot-receipt";
        const heading = document.createElement("div"); heading.className = "loot-receipt-heading";
        const task = document.createElement("strong"); task.textContent = taskName(receipt.task);
        const state = document.createElement("span"); state.className = `loot-receipt-state${receipt.unidentified ? " incomplete" : ""}`;
        const reviewLabels = { unidentified_items: "some item details unread", unverified_coverage: "full list not verified", missing_details: "no item details saved" };
        state.textContent = receipt.unidentified ? (reviewLabels[receipt.review_reason] || "needs a look") : "counted";
        heading.append(task, state);
        const time = document.createElement("time"); const when = dateValue(receipt.time);
        time.textContent = when ? when.toLocaleString() : "time unavailable";
        if (when) time.dateTime = when.toISOString();
        const detail = document.createElement("p"); detail.className = "loot-receipt-detail"; detail.textContent = receipt.detail;
        row.append(heading, time);
        if (receipt.items?.length) {
          const items = document.createElement("ul"); items.className = "loot-receipt-items"; items.setAttribute("aria-label", "Items received");
          receipt.items.forEach((item) => {
            const line = document.createElement("li");
            const name = document.createElement("span"); name.textContent = item.name;
            const quantity = document.createElement("strong"); quantity.textContent = quantityText(item.quantity);
            line.append(makeIcon(item), name, quantity); items.append(line);
          });
          row.append(items);
        } else {
          const unread = document.createElement("p"); unread.className = "loot-receipt-empty";
          unread.textContent = "reward saved. item details still need a look."; row.append(unread);
        }
        if (receipt.detail) {
          const note = document.createElement("details"); note.className = "loot-receipt-note";
          const label = document.createElement("summary"); label.textContent = "original action log";
          note.append(label, detail); row.append(note);
        }
        if (/^\/api\/loot\/[a-f0-9]{32}\/receipt$/.test(receipt.receipt_url || "")) {
          const link = document.createElement("a"); link.className = "loot-receipt-image"; link.href = receipt.receipt_url; link.target = "_blank"; link.rel = "noopener";
          const img = document.createElement("img"); img.src = receipt.receipt_url; img.alt = "Saved reward receipt"; img.loading = "lazy";
          const label = document.createElement("span"); label.textContent = "open receipt ↗";
          link.append(img, label); row.append(link);
        }
        rows.append(row);
      });
      $("loot-receipts").replaceChildren(rows);
    } catch { $("loot-since").textContent = "couldn't read the loot totals. trying again shortly."; }
    finally { lootLoading = false; }
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

  $("run-available").addEventListener("click", () => void post("/api/run-available", {}, "checking what’s ready. everything runs in order."));
  $("run-restart").addEventListener("click", () => void post("/api/run", { task: "restart" }, "restart’s in the queue."));
  $("run-daily").addEventListener("click", () => void post("/api/run", { task: "daily" }, "daily’s in the queue."));
  $("run-club").addEventListener("click", () => void post("/api/run", { task: "club" }, "club check-in is in the queue."));
  $("run-cafe").addEventListener("click", () => void post("/api/run", { task: "cafe" }, "café’s in the queue."));
  $("run-ap").addEventListener("click", () => void post("/api/run", { task: "spend_ap" }, "AP spending is in the queue."));
  $("run-total-assault").addEventListener("click", () => void post("/api/run", { task: "total_assault" }, "total assault is in the queue. mock battle first."));
  $("run-tactical").addEventListener("click", () => void post("/api/run", { task: "tactical_rewards" }, "tactical rewards are in the queue. battles come later."));
  $("run-red-dots").addEventListener("click", () => void post("/api/run", { task: "red_dots" }, "checking the red dots. anything to collect goes in the queue."));
  $("run-free-pack").addEventListener("click", () => void post("/api/run", { task: "free_pack" }, "free pack + mail are in the queue."));
  $("run-tasks").addEventListener("click", () => void post("/api/run", { task: "tasks" }, "task rewards are in the queue."));
  $("run-mail").addEventListener("click", () => void post("/api/run", { task: "mail" }, "mail’s in the queue."));
  $("run-packs").addEventListener("click", () => void post("/api/run", { task: "packs" }, "pack check and mail are in the queue."));
  $("run-crafting").addEventListener("click", () => void post("/api/run", { task: "crafting" }, "crafting’s in the queue."));
  $("run-bounties").addEventListener("click", () => void post("/api/run", { task: "bounties" }, "bounties are in the queue."));
  $("run-scrimmages").addEventListener("click", () => void post("/api/run", { task: "scrimmages" }, "scrimmages are in the queue."));
  $("run-lessons").addEventListener("click", () => void post("/api/run", { task: "lessons" }, "lessons are in the queue."));
  $("stop-run").addEventListener("click", () => void post("/api/stop", {}, "stopping this task and pausing the queue. everything waiting stays there."));
  $("pause-queue").addEventListener("click", () => void post("/api/pause", {}, isRunning()
    ? "pausing after this job. it’s still finishing; no next job will start."
    : "queue paused. hit do everything when you’re ready to get going again."));
  $("resume-queue").addEventListener("click", () => void post("/api/resume", {}, "queue’s running again."));
  $("capture").addEventListener("click", () => void post("/api/capture", {}));
  $("popup-show-more").addEventListener("click", () => { popupLimit += 6; renderPopups(); });
  $("clear-loot").addEventListener("click", async () => {
    if (await post("/api/clear-loot", {}, "fresh count. the history and receipts are still there.")) await loadLoot();
  });
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
    if (!$("settings-form").reportValidity()) return;
    const values = Object.fromEntries(settingNames.map((name) => {
      const value = booleanSettings.has(name) ? fields[name].checked
        : name.endsWith("_max_cents") ? Math.round(Number(fields[name].value) * 100)
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
