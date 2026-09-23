"use strict";
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const state = {
  meetings: [],
  current: null,
  filter: "all",
  dirty: false,
  transcriptDirty: false,
  view: "list",
  recorder: null,
  recorded: null,
  stream: null,
};
const statuses = {
  uploaded: "Запись загружена",
  queued: "В очереди",
  processing: "В обработке",
  transcribed: "Транскрипт готов",
  ready: "Протокол готов",
  error: "Нужна проверка",
};
const stages = {
  transcribe: "Распознаём речь",
  diarize: "Разделяем говорящих",
  analyze: "Готовим протокол",
};
const time = (s) =>
  `${Math.floor(s / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(s % 60)
    .toString()
    .padStart(2, "0")}`;
const busy = (m) => ["queued", "processing"].includes(m.status);
const badge = (m) =>
  `<span class="badge ${busy(m) ? "processing" : m.status === "error" ? "error" : m.status === "uploaded" ? "warn" : ""}">${esc(statuses[m.status] || m.status)}</span>`;
function toast(message, error = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = `toast ${error ? "error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), 6000);
}
async function api(url, options = {}) {
  const r = await fetch(url, options);
  if (!r.ok) {
    let e;
    try {
      e = await r.json();
    } catch {
      e = { detail: r.statusText };
    }
    throw Error(
      typeof e.detail === "string" ? e.detail : "Проверьте заполнение полей",
    );
  }
  return r.status === 204 ? null : r.json();
}
function json(method, body) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}
async function refreshList() {
  state.meetings = await api("/api/meetings");
  renderList();
}
function renderList() {
  $("#stat-total").textContent = state.meetings.length;
  $("#nav-count").textContent = state.meetings.length;
  $("#stat-tasks").textContent = state.meetings.reduce(
    (n, m) => n + m.task_count,
    0,
  );
  $("#stat-ready").textContent = state.meetings.filter((m) =>
    ["transcribed", "ready"].includes(m.status),
  ).length;
  const search = $("#search").value.toLowerCase();
  const filtered = state.meetings.filter(
    (m) =>
      (state.filter === "all" ||
        (state.filter === "processing"
          ? busy(m)
          : m.status === state.filter)) &&
      m.title.toLowerCase().includes(search),
  );
  $("#empty-state").classList.toggle("hidden", state.meetings.length > 0);
  $("#meeting-list").innerHTML =
    filtered
      .map(
        (m) =>
          `<button class="meeting-card" data-meeting="${m.id}" type="button"><div class="meeting-symbol">▤</div><div><h3>${esc(m.title)}</h3><div class="meta">${esc(m.meeting_date)} · ${time(m.duration)} · ${m.participants.length} участн. · ${m.task_count} поруч.</div></div>${badge(m)}<span class="meta">↗</span></button>`,
      )
      .join("") ||
    (state.meetings.length
      ? '<div class="placeholder">По вашему запросу встреч нет.</div>'
      : "");
}
function showView(view) {
  state.view = view;
  for (const v of ["list", "detail", "tasks"])
    $(`#${v}-view`).classList.toggle("hidden", view !== v);
  $("#nav-meetings").classList.toggle("active", view !== "tasks");
  $("#nav-tasks").classList.toggle("active", view === "tasks");
  $("#breadcrumb").textContent =
    view === "detail"
      ? "Совещания / Протокол встречи"
      : view === "tasks"
        ? "Рабочее пространство / Поручения"
        : "Рабочее пространство / Совещания";
}
function mayLeave() {
  return (
    !state.dirty || confirm("Есть несохранённые изменения. Покинуть страницу?")
  );
}
async function openMeeting(id) {
  if (!mayLeave()) return;
  state.current = await api(`/api/meetings/${id}`);
  state.dirty = false;
  state.transcriptDirty = false;
  showView("detail");
  renderDetail();
}
function renderDetail() {
  const m = state.current,
    p = m.protocol,
    isBusy = busy(m),
    hasText = m.segments.length > 0;
  $("#detail-view").innerHTML =
    `<button class="back-btn" data-action="back">← Все совещания</button>
 <div class="page-heading detail-heading"><div><div class="eyebrow">ПРОТОКОЛ СОВЕЩАНИЯ</div><h1>${esc(m.title)}</h1><div class="meta">${esc(m.meeting_date)} · ${time(m.duration)} · ${esc(m.participants.join(", ") || "Участники не указаны")}</div></div><div class="actions">${badge(m)}<button class="secondary" data-action="delete" ${isBusy ? "disabled" : ""}>Удалить</button></div></div>
 ${m.error ? `<div class="notice error" role="alert">${esc(m.error)}</div>` : ""}
 <div class="panel"><div class="panel-title"><h3>Обработка записи</h3><span class="meta">Последовательно · на вашем Mac</span></div><div class="pipeline">
 <select id="language" aria-label="Язык распознавания" ${isBusy ? "disabled" : ""}><option value="kk" ${m.language === "kk" || !m.language ? "selected" : ""}>Казахский + RU</option><option value="ru" ${m.language === "ru" ? "selected" : ""}>Русский + ҚАЗ</option><option value="auto" ${m.language === "auto" ? "selected" : ""}>Автоопределение</option></select>
 <button class="primary" data-stage="transcribe" ${isBusy ? "disabled" : ""}>1. Распознать</button><span class="meta">→</span><button class="secondary" data-stage="diarize" ${isBusy || !hasText ? "disabled" : ""}>2. Разделить голоса</button><span class="meta">→</span><button class="secondary" data-stage="analyze" ${isBusy || !hasText ? "disabled" : ""}>3. Создать протокол</button></div>
 ${
   isBusy
     ? `<div class="loading-line pulse">◌ ${m.status === "queued" ? "Ожидаем свободную память" : esc(stages[m.stage])}… Можно открыть другую встречу.</div>`
     : `<p class="small-note">Язык задаёт настройку модели, а не гарантированную поддержку смешанной речи. ${Object.entries(
         m.timings || {},
       )
         .map(([k, v]) => `${esc(stages[k])}: ${v} с`)
         .join(" · ")}</p>`
 }</div>
 <div class="detail-grid"><div class="panel"><div class="panel-title"><h3>Транскрипт <span class="meta">${m.segments.length} реплик</span></h3><button class="secondary" data-action="save-transcript" ${!hasText || isBusy ? "disabled" : ""}>Сохранить текст</button></div>
 <audio controls class="audio-player" id="player" src="/api/meetings/${m.id}/audio" preload="metadata"></audio>
 <div class="speakers">${Object.entries(m.speakers)
   .map(
     ([id, name]) =>
       `<label class="speaker-edit">${esc(id)}<input data-speaker-name="${esc(id)}" value="${esc(name)}" maxlength="200" ${isBusy ? "disabled" : ""}></label>`,
   )
   .join("")}</div>
 ${hasText && !m.diarized ? '<p class="small-note">Голоса ещё не разделены автоматически. Можно запустить этап 2.</p>' : ""}
 <div class="transcript-list">${
   m.segments
     .map(
       (s) =>
         `<div class="segment" data-segment="${s.id}"><div class="segment-head"><button class="time-btn" data-seek="${s.start}">▶ ${time(s.start)}</button><select data-segment-speaker ${isBusy ? "disabled" : ""} aria-label="Говорящий">${Object.entries(
           m.speakers,
         )
           .map(
             ([id, name]) =>
               `<option value="${esc(id)}" ${id === s.speaker ? "selected" : ""}>${esc(name)}</option>`,
           )
           .join(
             "",
           )}</select>${s.uncertain ? '<span class="uncertain">Проверьте фрагмент</span>' : ""}</div><textarea data-segment-text aria-label="Текст реплики" ${isBusy ? "disabled" : ""}>${esc(s.text)}</textarea></div>`,
     )
     .join("") ||
   '<div class="placeholder">Здесь появятся реплики и временные метки.<br>Нажмите «Распознать», чтобы начать.</div>'
 }</div></div>
 <div><div class="panel"><div class="panel-title"><h3>Итоги встречи</h3><span class="badge ${p?.approved ? "" : "warn"}">${p?.approved ? "Подтверждено" : "Черновик"}</span></div>
 ${(p?.approved ? [] : m.warnings).map((w) => `<p class="small-note">${esc(w)}</p>`).join("")}
 ${p ? `<label>Краткое содержание<textarea id="summary" class="summary-area" ${isBusy ? "disabled" : ""}>${esc(p.summary)}</textarea></label><label>Решения <span class="meta">по одному на строку</span><textarea id="decisions" ${isBusy ? "disabled" : ""}>${esc(p.decisions.join("\n"))}</textarea></label><div class="panel-title subheading"><span>Поручения · ${p.tasks.length}</span><button class="text-btn" data-action="add-task" ${isBusy ? "disabled" : ""}>＋ Добавить вручную</button></div><div id="task-list">${p.tasks.map((t, i) => taskHTML(t, i, m)).join("") || '<p class="small-note">Поручения не обнаружены. Если они есть в записи, добавьте их вручную.</p>'}</div><div class="protocol-actions"><button class="secondary" data-action="save-protocol" ${isBusy ? "disabled" : ""}>Сохранить черновик</button><button class="primary" data-action="approve" ${isBusy ? "disabled" : ""}>Подтвердить протокол</button></div>` : `<div class="placeholder">После проверки текста нажмите<br>«Создать протокол».<br>ИИ выделит решения и поручения.</div>`}
 </div><div class="panel"><div class="panel-title"><h3>Экспорт протокола</h3><span class="meta">Сохранённая версия</span></div><p class="small-note">${p?.approved ? "Подтверждённый протокол готов к передаче." : "Неподтверждённый документ будет помечен как черновик."}</p><div class="actions"><button class="secondary" data-export="docx" ${!hasText || isBusy ? "disabled" : ""}>↓ DOCX</button><button class="secondary" data-export="pdf" ${!hasText || isBusy ? "disabled" : ""}>↓ PDF</button><button class="secondary" data-export="json" ${!hasText || isBusy ? "disabled" : ""}>↓ JSON</button></div></div></div></div>`;
  if (isBusy)
    $$(
      "#detail-view input, #detail-view textarea, #detail-view select",
    ).forEach((el) => (el.disabled = true));
}
function taskHTML(t, i, m) {
  const source = m.segments.find((s) => t.source_ids.includes(s.id));
  return `<article class="task-card" data-task="${i}"><textarea data-field="title" aria-label="Суть поручения">${esc(t.title)}</textarea><div class="task-fields"><label>Ответственный<input data-field="owner" value="${esc(t.owner)}" placeholder="Нужно уточнить"></label><label>Срок из разговора<input data-field="deadline_text" value="${esc(t.deadline_text)}" placeholder="Не указан"></label><label>Подтверждённая дата<input type="date" data-field="due_date" value="${esc(t.due_date)}"></label></div>${t.evidence ? `<div class="task-evidence">«${esc(t.evidence)}» ${source ? `<button class="time-btn" data-seek="${source.start}">▶ ${time(source.start)}</button>` : ""}</div>` : ""}<div class="task-footer"><label class="check-label"><input type="checkbox" data-field="reviewed" ${!t.needs_review ? "checked" : ""}>Проверено</label><select data-field="status" aria-label="Статус поручения"><option value="open" ${t.status === "open" ? "selected" : ""}>В работе</option><option value="done" ${t.status === "done" ? "selected" : ""}>Выполнено</option></select><button class="icon-btn" data-remove-task="${i}" aria-label="Удалить поручение">×</button></div></article>`;
}
function readProtocol() {
  const original = state.current.protocol;
  return {
    ...original,
    summary: $("#summary").value,
    decisions: $("#decisions")
      .value.split("\n")
      .map((s) => s.trim())
      .filter(Boolean),
    approved: false,
    tasks: $$("[data-task]").map((el) => {
      const task = { ...original.tasks[Number(el.dataset.task)] };
      for (const field of [
        "title",
        "owner",
        "deadline_text",
        "due_date",
        "status",
      ])
        task[field] = $(`[data-field="${field}"]`, el).value.trim() || null;
      task.needs_review = !$('[data-field="reviewed"]', el).checked;
      return task;
    }),
  };
}
async function saveTranscript() {
  const m = state.current;
  const speakers = Object.fromEntries(
    $$("[data-speaker-name]").map((el) => [
      el.dataset.speakerName,
      el.value.trim() || el.dataset.speakerName,
    ]),
  );
  const segments = $$("[data-segment]").map((el) => ({
    ...m.segments.find((s) => s.id === Number(el.dataset.segment)),
    text: $("[data-segment-text]", el).value,
    speaker: $("[data-segment-speaker]", el).value,
  }));
  if (
    m.protocol &&
    !confirm("Сохранение текста сбросит текущий протокол. Продолжить?")
  )
    return;
  state.current = await api(
    `/api/meetings/${m.id}/transcript`,
    json("PUT", { segments, speakers }),
  );
  state.dirty = false;
  state.transcriptDirty = false;
  renderDetail();
  toast("Транскрипт сохранён");
}
async function saveProtocol(approved = false) {
  if (state.transcriptDirty)
    throw Error("Сначала сохраните транскрипт и заново создайте протокол");
  const p = readProtocol();
  p.approved = approved;
  state.current = await api(
    `/api/meetings/${state.current.id}/protocol`,
    json("PUT", p),
  );
  state.dirty = false;
  renderDetail();
  toast(approved ? "Протокол подтверждён" : "Черновик сохранён");
}
async function showTasks() {
  if (!mayLeave()) return;
  showView("tasks");
  $("#all-tasks").innerHTML = '<p class="placeholder">Загружаем поручения…</p>';
  const meetings = await api("/api/meetings");
  const details = await Promise.all(
    meetings
      .filter((m) => m.task_count > 0)
      .map((m) => api(`/api/meetings/${m.id}`)),
  );
  const today = new Date().toLocaleDateString("en-CA");
  $("#all-tasks").innerHTML =
    details
      .flatMap((m) =>
        (m.protocol?.tasks || []).map(
          (t) =>
            `<article class="all-task"><h3>${esc(t.title)}</h3><div class="meta">${esc(t.owner || "Ответственный не указан")} · <span class="${t.due_date && t.due_date < today && t.status !== "done" ? "overdue" : ""}">${esc(t.due_date || t.deadline_text || "Срок не указан")}</span> · ${t.status === "done" ? "Выполнено" : "В работе"}${t.needs_review ? " · Требует проверки" : ""}</div><div class="actions"><button class="text-btn" data-meeting="${m.id}">${esc(m.title)} ↗</button></div></article>`,
        ),
      )
      .join("") ||
    '<div class="empty-state"><h2>Поручений пока нет</h2><p>Они появятся после подготовки протокола встречи.</p></div>';
}
function openUpload() {
  state.recorded = null;
  $("#upload-form").reset();
  $("#meeting-date").value = new Date().toLocaleDateString("en-CA");
  $("#file-label").textContent = "Выберите аудио или видео";
  $("#audio-file").required = true;
  $("#upload-dialog").showModal();
}
function stopRecording() {
  if (state.recorder?.state === "recording") state.recorder.stop();
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
}
async function record() {
  if (state.recorder?.state === "recording") {
    stopRecording();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia)
    throw Error("Для записи нужен localhost и браузер с поддержкой микрофона");
  state.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const chunks = [];
  const recorder = new MediaRecorder(state.stream);
  state.recorder = recorder;
  recorder.ondataavailable = (e) => {
    if (e.data.size) chunks.push(e.data);
  };
  recorder.onstop = () => {
    const type = recorder.mimeType;
    const ext = type.includes("mp4") ? "m4a" : "webm";
    state.recorded = new File(chunks, `meeting.${ext}`, { type });
    $("#file-label").textContent = "Запись микрофона готова";
    $("#audio-file").required = false;
    $("#record-mic").textContent = "● Записать заново";
    $("#record-status").textContent =
      `${(state.recorded.size / 1024).toFixed(0)} КБ · локальная запись`;
    state.stream?.getTracks().forEach((t) => t.stop());
  };
  recorder.start(1000);
  $("#record-mic").textContent = "■ Остановить запись";
  $("#record-status").textContent = "Идёт запись…";
}
async function showSystem() {
  $("#system-dialog").showModal();
  $("#system-content").textContent = "Проверяем…";
  const h = await api("/api/health");
  $("#system-content").innerHTML =
    Object.entries(h.models)
      .map(
        ([key, m]) =>
          `<div class="system-model"><div><strong>${{ asr: "Распознавание речи", llm: "Поручения и саммари", diarization: "Разделение голосов" }[key]}</strong><small>${esc(m.name)}</small></div><span class="badge ${m.cached ? "" : "warn"}">${m.cached ? "Скачана" : "Не скачана"}</span></div>`,
      )
      .join("") +
    `<p class="small-note">FFmpeg: ${h.ffmpeg ? "установлен" : "не найден"}</p>`;
}
document.addEventListener("click", async (e) => {
  const button = e.target.closest("button");
  if (!button || button.disabled) return;
  try {
    if (button.dataset.meeting) {
      await openMeeting(button.dataset.meeting);
      return;
    }
    if (button.dataset.seek) {
      const player = $("#player");
      player.currentTime = Number(button.dataset.seek);
      await player.play();
      return;
    }
    if (button.dataset.stage) {
      if (state.dirty) throw Error("Сначала сохраните изменения");
      if (
        button.dataset.stage === "transcribe" &&
        state.current.segments.length &&
        !confirm(
          "Повторное распознавание заменит текст и протокол. Продолжить?",
        )
      )
        return;
      state.current = await api(
        `/api/meetings/${state.current.id}/process`,
        json("POST", {
          stage: button.dataset.stage,
          language: $("#language").value,
        }),
      );
      renderDetail();
      return;
    }
    if (button.dataset.export) {
      if (state.dirty) throw Error("Сохраните изменения перед экспортом");
      window.location.href = `/api/meetings/${state.current.id}/export/${button.dataset.export}`;
      return;
    }
    if (button.dataset.removeTask !== undefined) {
      if (state.transcriptDirty) throw Error("Сначала сохраните транскрипт");
      state.current.protocol = readProtocol();
      state.current.protocol.tasks.splice(Number(button.dataset.removeTask), 1);
      state.dirty = true;
      renderDetail();
      return;
    }
    switch (button.dataset.action) {
      case "back":
        if (mayLeave()) {
          state.dirty = false;
          showView("list");
          await refreshList();
        }
        break;
      case "save-transcript":
        await saveTranscript();
        break;
      case "save-protocol":
        await saveProtocol();
        break;
      case "approve":
        await saveProtocol(true);
        break;
      case "add-task":
        if (state.transcriptDirty) throw Error("Сначала сохраните транскрипт");
        state.current.protocol = readProtocol();
        state.current.protocol.tasks.push({
          title: "",
          owner: null,
          deadline_text: null,
          due_date: null,
          source_ids: [],
          evidence: "",
          status: "open",
          needs_review: true,
        });
        state.dirty = true;
        renderDetail();
        break;
      case "delete":
        if (confirm("Удалить встречу, аудио и протокол с этого компьютера?")) {
          await api(`/api/meetings/${state.current.id}`, { method: "DELETE" });
          state.dirty = false;
          state.current = null;
          showView("list");
          await refreshList();
        }
        break;
    }
  } catch (err) {
    toast(err.message, true);
  }
});
$("#detail-view").addEventListener("input", (e) => {
  if (e.target.matches("input,textarea,select") && e.target.id !== "language") {
    state.dirty = true;
    if (
      e.target.matches(
        "[data-segment-text],[data-segment-speaker],[data-speaker-name]",
      )
    )
      state.transcriptDirty = true;
    if (state.current.protocol) state.current.protocol.approved = false;
  }
});
$("#new-meeting").onclick = openUpload;
$("#empty-new").onclick = openUpload;
$("#close-upload").onclick = () => {
  stopRecording();
  $("#upload-dialog").close();
};
$("#upload-dialog").addEventListener("cancel", stopRecording);
$("#audio-file").onchange = (e) => {
  state.recorded = null;
  $("#file-label").textContent =
    e.target.files[0]?.name || "Выберите аудио или видео";
};
$("#record-mic").onclick = () => record().catch((e) => toast(e.message, true));
$("#upload-form").onsubmit = async (e) => {
  e.preventDefault();
  if (state.recorder?.state === "recording") {
    toast("Сначала остановите запись", true);
    return;
  }
  const form = new FormData(e.target);
  if (state.recorded) form.set("file", state.recorded);
  const file = form.get("file");
  if (!file?.size) {
    toast("Выберите непустой файл", true);
    return;
  }
  if (file.size > 250 * 1024 * 1024) {
    toast("Максимальный размер — 250 МБ", true);
    return;
  }
  const btn = $("#upload-submit");
  btn.disabled = true;
  btn.textContent = "Подготавливаем аудио…";
  try {
    const m = await api("/api/meetings", { method: "POST", body: form });
    $("#upload-dialog").close();
    await openMeeting(m.id);
    await refreshList();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Создать совещание →";
  }
};
$("#search").oninput = renderList;
$$("[data-filter]").forEach(
  (b) =>
    (b.onclick = () => {
      state.filter = b.dataset.filter;
      $$("[data-filter]").forEach((x) => x.classList.toggle("active", x === b));
      renderList();
    }),
);
$("#nav-meetings").onclick = async () => {
  if (mayLeave()) {
    state.dirty = false;
    showView("list");
    await refreshList();
  }
};
$("#nav-tasks").onclick = () =>
  showTasks().catch((e) => toast(e.message, true));
$("#show-system").onclick = () =>
  showSystem().catch((e) => toast(e.message, true));
$("#close-system").onclick = () => $("#system-dialog").close();
window.addEventListener("beforeunload", (e) => {
  if (state.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
setInterval(async () => {
  try {
    if (state.view === "detail" && state.current && busy(state.current)) {
      const m = await api(`/api/meetings/${state.current.id}`);
      if (
        m.status !== state.current.status ||
        m.stage !== state.current.stage
      ) {
        state.current = m;
        renderDetail();
      }
    } else if (state.view === "list" && !$("#upload-dialog").open) {
      await refreshList();
    }
  } catch {}
}, 2500);
refreshList().catch((e) => toast(e.message, true));

$("#mobile-meetings").onclick = () => $("#nav-meetings").click();
$("#mobile-tasks").onclick = () => $("#nav-tasks").click();
$("#mobile-system").onclick = () => $("#show-system").click();
