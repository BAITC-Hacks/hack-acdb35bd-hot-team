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
  prepare: "Речь и голоса",
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
  `<span class="badge ${busy(m) ? "processing" : m.status === "error" ? "error" : m.status === "uploaded" ? "warn" : ""}">${esc(statuses[m.status] || m.status)}${busy(m) && m.progress ? ` · ${m.progress.estimated ? "≈" : ""}${Number(m.progress.percent) || 0}%` : ""}</span>`;
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
          `<div class="meeting-row"><button class="meeting-card" data-meeting="${m.id}" type="button"><div class="meeting-symbol">▤</div><div><h3>${esc(m.title)}</h3><div class="meta">${esc(m.meeting_date)} · ${time(m.duration)} · ${m.participants.length} участн. · ${m.task_count} поруч.</div></div>${badge(m)}<span class="meta">↗</span></button><button class="icon-btn meeting-trash" type="button" data-delete-meeting="${m.id}" aria-label="Удалить встречу: ${esc(m.title)}" title="${busy(m) ? "Дождитесь завершения обработки" : "Удалить встречу"}" ${busy(m) ? "disabled" : ""}><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6"/></svg></button></div>`,
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
function confirmMeetingDeletion(meeting) {
  return new Promise(resolve => {
    const dialog = document.createElement("dialog");
    dialog.setAttribute("aria-labelledby", "delete-meeting-title");
    dialog.innerHTML = `<form method="dialog"><h2 id="delete-meeting-title">Удалить встречу?</h2>
      <p><strong>${esc(meeting.title)}</strong></p>
      <p>Будут удалены запись, транскрипт, протокол и поручения с доски. Восстановить встречу будет нельзя.</p>
      <p class="small-note">Напоминания Telegram будут отменены. Уже отправленные сообщения останутся в чате.</p>
      <div class="actions"><button class="secondary" value="cancel" autofocus>Отмена</button><button class="secondary meeting-delete" value="delete">Удалить безвозвратно</button></div></form>`;
    dialog.addEventListener("close", () => {
      const confirmed = dialog.returnValue === "delete";
      dialog.remove();
      resolve(confirmed);
    }, {once:true});
    document.body.append(dialog);
    dialog.showModal();
  });
}

async function deleteMeeting(id) {
  const m = state.meetings.find(m => m.id === id) || (state.current?.id === id ? state.current : null);
  if (!m || busy(m)) return;
  if (!(await confirmMeetingDeletion(m))) return;
  await api(`/api/meetings/${id}`, {method: "DELETE"});
  if (state.current?.id === id) {
    state.current = null;
    state.dirty = false;
    state.transcriptDirty = false;
  }
  showView("list");
  await refreshList();
  toast("Встреча удалена");
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
 <div class="page-heading detail-heading"><div><div class="eyebrow">ПРОТОКОЛ СОВЕЩАНИЯ</div><h1>${esc(m.title)}</h1><div class="meta">${esc(m.meeting_date)} · ${time(m.duration)} · ${esc(m.participants.join(", ") || "Участники не указаны")}</div></div><div class="actions"><span id="current-status">${badge(m)}</span><button class="secondary" data-action="delete" ${isBusy ? "disabled" : ""}>Удалить</button></div></div>
 ${m.error ? `<div class="notice error" role="alert">${esc(m.error)}</div>` : ""}
 <div class="panel"><div class="panel-title"><h3>Обработка записи</h3><span class="meta">Последовательно · на вашем Mac</span></div><div class="pipeline">
 <select id="language" aria-label="Язык распознавания" ${isBusy ? "disabled" : ""}><option value="kk" ${m.language === "kk" || !m.language ? "selected" : ""}>Казахский + RU</option><option value="ru" ${m.language === "ru" ? "selected" : ""}>Русский + ҚАЗ</option><option value="ru_kk" ${m.language === "ru_kk" ? "selected" : ""}>Сравнить ҚАЗ / RU · два прохода</option><option value="auto" ${m.language === "auto" ? "selected" : ""}>Автоопределение</option></select>
 <button class="primary" data-stage="prepare" ${isBusy ? "disabled" : ""}>1. Распознать и разделить голоса</button><span class="meta">→</span><button class="secondary" data-stage="analyze" ${isBusy || !hasText ? "disabled" : ""}>2. Создать протокол</button></div>
 ${
   isBusy
     ? ""
     : `<p class="small-note">Язык задаёт настройку модели, а не гарантированную поддержку смешанной речи. ${Object.entries(
         m.timings || {},
       )
         .map(([k, v]) => `${esc(stages[k])}: ${v} с`)
         .join(" · ")}</p>`
 }<div id="stage-progress">${progressHTML(m)}</div></div>
 <div class="detail-grid"><div class="panel"><div class="panel-title"><h3>Транскрипт <span class="meta">${m.segments.length} реплик</span></h3><button class="secondary" data-action="save-transcript" ${!hasText || isBusy ? "disabled" : ""}>Сохранить текст</button></div>
 <button class="secondary" data-action="voices" ${isBusy ? "disabled" : ""}>Голосовые профили · узнать участников</button><audio controls class="audio-player" id="player" src="/api/meetings/${m.id}/audio" preload="metadata"></audio>
 <p class="small-note">Исполнители определяются по обращениям в разговоре. Подписывать каждый голос для получения поручений не обязательно.</p><details><summary>Имена голосов · необязательно</summary><div class="speakers">${Object.entries(m.speakers)
   .map(
     ([id, name]) =>
       `<label class="speaker-edit">${esc(id)}<input data-speaker-name="${esc(id)}" value="${esc(name)}" maxlength="200" ${isBusy ? "disabled" : ""}></label>`,
   )
   .join("")}</div></details>
 ${hasText && !m.diarized ? '<p class="small-note">Голоса ещё не разделены автоматически. Запустите «Распознать и разделить голоса».</p>' : ""}
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
           )}</select>${s.uncertain ? '<span class="uncertain">Проверьте фрагмент</span>' : ""}</div><textarea data-segment-text aria-label="Текст реплики" ${isBusy ? "disabled" : ""}>${esc(s.text)}</textarea>${s.alternative_text ? `<details><summary>Другой вариант · RU · ${time(s.start)}–${time(s.end)}</summary><p class="small-note">${esc(s.alternative_text)}</p><p class="small-note">Сверьте с аудио и при необходимости исправьте текст выше. Вариант не является исправлением.</p></details>` : ""}</div>`,
     )
     .join("") ||
   '<div class="placeholder">Здесь появятся реплики и временные метки.<br>Нажмите «Распознать», чтобы начать.</div>'
 }</div></div>
 <div><div class="panel"><div class="panel-title"><h3>Итоги встречи</h3><span class="badge ${p?.approved ? "" : "warn"}">${p?.approved ? "Подтверждено" : "Черновик"}</span></div>
 ${(p?.approved ? [] : m.warnings).map((w) => `<p class="small-note">${esc(w)}</p>`).join("")}
 ${p ? `<label>Краткое содержание<textarea id="summary" class="summary-area" ${isBusy ? "disabled" : ""}>${esc(p.summary)}</textarea></label>${sourcesHTML(p, m)}<label>Решения <span class="meta">по одному на строку</span><textarea id="decisions" ${isBusy ? "disabled" : ""}>${esc(p.decisions.join("\n"))}</textarea></label><div class="panel-title subheading"><span>Поручения · ${p.tasks.length}</span><button class="text-btn" data-action="add-task" ${isBusy ? "disabled" : ""}>＋ Добавить вручную</button></div><div id="task-list">${p.tasks.map((t, i) => taskHTML(t, i, m)).join("") || '<p class="small-note">Поручения не обнаружены. Если они есть в записи, добавьте их вручную.</p>'}</div><div class="protocol-actions"><button class="secondary" data-action="classify" ${isBusy ? "disabled" : ""}>Определить срочность и направление</button><button class="secondary" data-action="save-protocol" ${isBusy ? "disabled" : ""}>Сохранить черновик</button><button class="primary" data-action="approve" ${isBusy ? "disabled" : ""}>Подтвердить протокол</button></div>` : `<div class="placeholder">После проверки текста нажмите<br>«Создать протокол».<br>ИИ выделит решения и поручения.</div>`}
 </div><div class="panel"><div class="panel-title"><h3>Экспорт протокола</h3><span class="meta">Сохранённая версия</span></div><p class="small-note">${p?.approved ? "Подтверждённый протокол готов к передаче." : "Неподтверждённый документ будет помечен как черновик."}</p><button class="secondary" data-action="telegram">Telegram · уведомления</button><div class="actions"><button class="secondary" data-export="docx" ${!hasText || isBusy ? "disabled" : ""}>↓ DOCX</button><button class="secondary" data-export="pdf" ${!hasText || isBusy ? "disabled" : ""}>↓ PDF</button><button class="secondary" data-export="json" ${!hasText || isBusy ? "disabled" : ""}>↓ JSON</button></div></div></div></div>`;
  if (isBusy)
    $$(
      "#detail-view input, #detail-view textarea, #detail-view select",
    ).forEach((el) => (el.disabled = true));
}
const priorities = {unspecified: "Не определена", normal: "Обычная", high: "Высокая"};
const directions = {other: "Не определено", finance: "Финансы", procurement: "Закупки", legal: "Юридические вопросы", safety: "Безопасность", operations: "Производство", it: "ИТ", hr: "Персонал"};
function classificationFields(t) {
  return `<div class="task-fields">${[["priority", "Срочность", priorities, "unspecified"], ["direction", "Направление", directions, "other"]].map(([field,label,values,fallback]) => `<label>${label}<select data-field="${field}">${Object.entries(values).map(([key,name]) => `<option value="${key}" ${(t[field] || fallback) === key ? "selected" : ""}>${name}</option>`).join("")}</select></label>`).join("")}</div><p class="small-note">${esc(t.classification_reason || "Категории можно назначить вручную или определить по тексту.")}</p>`;
}
function taskHTML(t, i, m) {
  const source = m.segments.find((s) => t.source_ids.includes(s.id));
  return `<article class="task-card" data-task="${i}"><textarea data-field="title" aria-label="Суть поручения">${esc(t.title)}</textarea><div class="task-fields"><label>Ответственный<input data-field="owner" value="${esc(t.owner)}" placeholder="Нужно уточнить"></label><label>Срок из разговора<input data-field="deadline_text" value="${esc(t.deadline_text)}" placeholder="Не указан"></label><label>Подтверждённая дата<input type="date" data-field="due_date" value="${esc(t.due_date)}"></label></div>${t.evidence ? `<div class="task-evidence">«${esc(t.evidence)}» ${source ? `<button class="time-btn" data-seek="${source.start}">▶ ${time(source.start)}</button>` : ""}</div>` : ""}${taskContextHTML(t, m)}${classificationFields(t)}<div class="task-footer"><label class="check-label"><input type="checkbox" data-field="reviewed" ${!t.needs_review ? "checked" : ""}>Проверено</label><select data-field="status" aria-label="Статус поручения"><option value="open" ${t.status === "open" ? "selected" : ""}>К выполнению</option><option value="in_progress" ${t.status === "in_progress" ? "selected" : ""}>В работе</option><option value="done" ${t.status === "done" ? "selected" : ""}>Выполнено</option></select><button class="icon-btn" data-remove-task="${i}" aria-label="Удалить поручение">×</button></div></article>`;
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
      "priority",
      "direction",
      ])
        task[field] = $(`[data-field="${field}"]`, el).value.trim() || null;
      if (task.priority !== original.tasks[Number(el.dataset.task)].priority || task.direction !== original.tasks[Number(el.dataset.task)].direction)
        task.classification_reason = "Категории уточнены пользователем.";
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
const taskColumns = {open: "К выполнению", in_progress: "В работе", done: "Выполнено"};
let boardTasks = [], boardMoving = false, draggedTask = null;
async function showTasks() {
  if (!mayLeave()) return;
  showView("tasks");
  $("#all-tasks").innerHTML = '<p class="placeholder">Загружаем поручения…</p>';
  await refreshBoard();
}
async function refreshBoard(quiet = false) {
  const rows = await api("/api/tasks");
  if (state.view !== "tasks" || draggedTask || boardMoving) return;
  if (quiet && JSON.stringify(rows) === JSON.stringify(boardTasks)) return;
  boardTasks = rows;
  renderBoard();
}
function renderBoard() {
  const query = $("#task-search").value.trim().toLocaleLowerCase();
  const priority = $("#priority-filter").value, direction = $("#direction-filter").value;
  const tasks = boardTasks.filter(t => (!priority || (t.priority || "unspecified") === priority) && (!direction || (t.direction || "other") === direction) && [t.title, t.owner, t.meeting_title].some(v => (v || "").toLocaleLowerCase().includes(query)));
  const today = new Date().toLocaleDateString("en-CA");
  $("#board-count").textContent = `${tasks.length} поручений · ${tasks.filter(t => !t.approved || t.needs_review).length} требуют подтверждения`;
  $("#all-tasks").innerHTML = `<div class="kanban-board">${Object.entries(taskColumns).map(([status,label]) => {
    const rows = tasks.filter(t => (t.status || "open") === status);
    return `<section class="kanban-column" data-column="${status}" aria-label="${label}"><div class="kanban-heading"><h2>${label}</h2><span class="badge">${rows.length}</span></div><div class="kanban-cards">${rows.map(t => {
      const locked = !t.approved || t.needs_review || t.busy || boardMoving;
      const overdue = t.due_date && t.due_date < today && t.status !== "done";
      return `<article class="kanban-card" draggable="${!locked}" data-board-task="${t.id}" data-board-meeting="${t.meeting_id}"><div class="kanban-card-heading"><h3>${esc(t.title)}</h3><span class="kanban-grip" aria-hidden="true" title="${locked ? "Перемещение недоступно" : "Зажмите карточку и перенесите в другую колонку"}">⠿</span></div><p class="task-tags"><span class="badge ${t.priority === "high" ? "priority-high" : ""}">${esc(priorities[t.priority] || "Срочность не определена")}</span> <span class="badge">${esc(directions[t.direction] || "Направление не определено")}</span></p><p class="meta">${esc(t.owner || "Исполнитель не указан")}</p><p class="${overdue ? "overdue" : "meta"}">${overdue ? "Просрочено · " : "Срок · "}${esc(t.due_date || t.deadline_text || "не указан")}</p>${!t.approved || t.needs_review ? '<p class="small-note">Перемещение недоступно: проверьте поручения и подтвердите протокол встречи</p>' : ""}${t.busy ? '<p class="small-note">Встреча обрабатывается</p>' : ""}<button class="text-btn kanban-source" data-meeting="${t.meeting_id}">${esc(t.meeting_title)} ↗</button><label class="kanban-status">Статус<select data-board-status aria-label="Статус: ${esc(t.title)}" ${locked ? "disabled" : ""}>${Object.entries(taskColumns).map(([value,text]) => `<option value="${value}" ${value === t.status ? "selected" : ""}>${text}</option>`).join("")}</select></label></article>`;
    }).join("") || '<p class="kanban-empty">Пока нет поручений</p>'}</div></section>`;
  }).join("")}</div>`;
}
async function moveBoardTask(meeting, id, status) {
  if (boardMoving) return;
  const task = boardTasks.find(t => t.id === id && t.meeting_id === meeting);
  if (!task || task.status === status || !taskColumns[status]) return;
  boardMoving = true;
  renderBoard();
  try {
    await api(`/api/meetings/${meeting}/tasks/${id}`, json("PATCH", {status, expected_status: task.status}));
    toast(`Поручение: ${taskColumns[status]}`);
  } catch (e) { toast(e.message, true); }
  finally {
    boardMoving = false;
    renderBoard();
    await refreshBoard().catch(e => toast(e.message, true));
  }
}
$("#task-search").addEventListener("input", renderBoard);
$("#priority-filter").onchange = renderBoard;
$("#direction-filter").onchange = renderBoard;
$("#refresh-board").onclick = () => refreshBoard().catch(e => toast(e.message, true));
$("#all-tasks").addEventListener("change", e => {
  if (!e.target.matches("[data-board-status]")) return;
  const card = e.target.closest("[data-board-task]");
  moveBoardTask(card.dataset.boardMeeting, card.dataset.boardTask, e.target.value);
});
function clearBoardDrag() {
  draggedTask = null;
  $$(".kanban-card.is-dragging").forEach(el => el.classList.remove("is-dragging"));
  $$(".kanban-column.drop-target").forEach(el => el.classList.remove("drop-target"));
  $("#all-tasks").classList.remove("is-dragging");
}
$("#all-tasks").addEventListener("dragstart", e => {
  const card = e.target.closest('[data-board-task][draggable="true"]');
  if (!card || boardMoving || e.target.closest("button, select, input, a")) {
    e.preventDefault();
    return;
  }
  draggedTask = {id: card.dataset.boardTask, meeting: card.dataset.boardMeeting,
    status: card.closest("[data-column]").dataset.column};
  e.dataTransfer.setData("text/plain", card.dataset.boardTask);
  e.dataTransfer.effectAllowed = "move";
  card.classList.add("is-dragging");
  $("#all-tasks").classList.add("is-dragging");
});
$("#all-tasks").addEventListener("dragover", e => {
  const column = e.target.closest("[data-column]");
  if (!draggedTask || !column) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = column.dataset.column === draggedTask.status ? "none" : "move";
  $$(".kanban-column.drop-target").forEach(el => {
    if (el !== column) el.classList.remove("drop-target");
  });
  column.classList.toggle("drop-target", column.dataset.column !== draggedTask.status);
});
$("#all-tasks").addEventListener("dragleave", e => {
  const column = e.target.closest("[data-column]");
  if (column && !column.contains(e.relatedTarget)) column.classList.remove("drop-target");
});
$("#all-tasks").addEventListener("drop", e => {
  const column = e.target.closest("[data-column]");
  if (!column || !draggedTask) return;
  e.preventDefault();
  const task = draggedTask;
  clearBoardDrag();
  moveBoardTask(task.meeting, task.id, column.dataset.column);
});
$("#all-tasks").addEventListener("dragend", clearBoardDrag);
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
async function showTelegram() {
  if (state.dirty) throw Error("Сначала сохраните изменения");
  const m = state.current;
  const status = await api(`/api/meetings/${m.id}/telegram`);
  let dialog = $("#telegram-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "telegram-dialog";
    document.body.append(dialog);
  }
  const owners = [...new Set((m.protocol?.tasks || []).map(t => t.owner).filter(Boolean))];
  dialog.innerHTML = `<div class="dialog-heading"><h2>Уведомления Telegram</h2><button class="icon-btn" data-action="tg-close" aria-label="Закрыть">×</button></div>
  <p class="small-note">Исполнителю отправляются текст поручения и срок через Telegram. Полный транскрипт и аудио не отправляются.</p>
  ${status.enabled ? `<label>Исполнитель<select id="tg-owner">${owners.map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join("")}</select></label>
  <button class="secondary" data-action="tg-invite" ${owners.length ? "" : "disabled"}>Создать персональную ссылку</button>
  <p class="small-note">Передайте ссылку выбранному исполнителю. Она действует 24 часа. Новая ссылка отменяет предыдущую привязку этого исполнителя в этом совещании.</p>
  <div id="tg-link"></div>
  <p>${status.links.map(l => `${esc(l.owner)}: ${l.connected ? "подключён" : "ожидает Start"}`).join("<br>") || "Пока никто не подключён"}</p>
  <div class="actions"><button class="secondary" data-action="telegram">Обновить подключения</button><button class="primary" data-action="tg-send" ${m.protocol?.approved ? "" : "disabled"}>Отправить поручения</button></div>
  <p class="small-note">Отправляются только сохранённые, подтверждённые, незавершённые поручения. Повторное нажатие не дублирует успешно отправленные сообщения.</p>` : '<p>Добавьте TELEGRAM_BOT_TOKEN в .env и перезапустите приложение. Инструкция — в README.</p>'}
  <div id="tg-result" role="status">${status.reminders_unknown ? "Доставка некоторых напоминаний не подтверждена. Проверьте чат; повтор можно назначить кнопкой в сообщении." : ""}</div>`;
  if (!dialog.open) dialog.showModal();
}

document.addEventListener("click", async (e) => {
  const button = e.target.closest("button");
  if (!button || button.disabled) return;
  try {
    if (button.dataset.deleteMeeting) {
      await deleteMeeting(button.dataset.deleteMeeting);
      return;
    }
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
        ["transcribe", "prepare"].includes(button.dataset.stage) &&
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
      case "voices":
        await showVoices();
        break;
      case "classify":
        if (state.dirty) throw Error("Сначала сохраните изменения");
        state.current = await api(`/api/meetings/${state.current.id}/classify`, json("POST", {}));
        renderDetail();
        toast("Категории предложены по тексту. Проверьте их и подтвердите протокол.");
        break;
      case "telegram":
        await showTelegram();
        break;
      case "tg-close":
        $("#telegram-dialog").close();
        break;
      case "tg-invite": {
        const result = await api(`/api/meetings/${state.current.id}/telegram/invite`, json("POST", {owner: $("#tg-owner").value}));
        $("#tg-link").innerHTML = `<label>Ссылка для исполнителя<input readonly value="${esc(result.url)}"></label>`;
        break;
      }
      case "tg-send": {
        button.disabled = true;
        try {
          const result = await api(`/api/meetings/${state.current.id}/telegram/notify`, json("POST", {}));
          const labels = {sent: "отправлено", already_sent: "уже отправлено", not_connected: "не подключён", unknown: "доставка не подтверждена — проверьте чат; повтор автоматически отключён"};
          $("#tg-result").textContent = result.results.map(r => `${r.owner || "Без исполнителя"}: ${labels[r.state]}`).join("; ") || "Нет открытых поручений";
        } finally { button.disabled = false; }
        break;
      }
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
        await deleteMeeting(state.current.id);
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
      } else {
        state.current = m;
        const panel = $("#stage-progress");
        if (panel) panel.innerHTML = progressHTML(m);
        const status = $("#current-status");
        if (status) status.innerHTML = badge(m);
      }
    } else if (state.view === "tasks" && !boardMoving && !draggedTask && !document.activeElement?.closest("#all-tasks")) {
      await refreshBoard(true);
    } else if (state.view === "list" && !$("#upload-dialog").open) {
      await refreshList();
    }
  } catch {}
}, 2500);
refreshList().catch((e) => toast(e.message, true));

$("#mobile-meetings").onclick = () => $("#nav-meetings").click();
$("#mobile-tasks").onclick = () => $("#nav-tasks").click();
$("#mobile-system").onclick = () => $("#show-system").click();

function sourcesHTML(protocol, meeting) {
  const groups = protocol.sources;
  if (!groups) return "";
  const rows = [...(groups.summary || []), ...(groups.decisions || [])];
  if (!rows.length) return "";
  return `<details><summary>Основания саммари и решений · ${rows.length}</summary>${rows.map((fact) => {
    const segment = meeting.segments.find((s) => fact.source_ids.includes(s.id));
    return `<p class="small-note"><strong>${esc(fact.text)}</strong><br>${segment ? `<button class="time-btn" data-seek="${segment.start}">▶ ${time(segment.start)}</button>` : ""} ${esc(fact.evidence)}</p>`;
  }).join("")}</details>`;
}

function taskContextHTML(task, meeting) {
  if (!task.context_evidence?.length) return "";
  return `<details><summary>Почему этот исполнитель и срок</summary>${task.context_evidence.map(q => {
    const segment = meeting.segments.find(s => s.id === q.source_id);
    return `<p class="small-note">${segment ? `<button class="time-btn" data-seek="${segment.start}">▶ ${time(segment.start)}</button>` : ""} ${esc(q.text)}</p>`;
  }).join("")}</details>`;
}

function progressHTML(m) {
  const p = m.progress;
  if (!p) return "";
  const percent = Math.max(0, Math.min(100, Number(p.percent) || 0));
  const stopped = m.status === "error";
  const seconds = p.started_at ? Math.max(0, Math.floor((Date.now() - Date.parse(p.started_at)) / 1000)) : 0;
  const label = stopped ? "Обработка остановлена" : p.label;
  return `<section class="stage-progress ${stopped ? "stopped" : ""}" aria-label="Прогресс обработки">
    <div class="progress-heading"><strong>${esc(stages[p.stage] || "Обработка")}</strong><strong>${p.estimated ? "≈ " : ""}${percent}%</strong></div>
    <progress max="100" value="${percent}" aria-label="${esc(stages[p.stage] || "Обработка")}" aria-valuetext="${p.estimated ? "Поэтапная оценка: " : ""}${percent}%"></progress>
    <div class="progress-caption" role="status" aria-live="polite">${esc(label || "Подготовка")}${busy(m) && p.started_at ? ` · ${time(seconds)}` : ""}</div>
    ${busy(m) ? `<p class="small-note">${m.status === "queued" ? "Ожидаем завершения другой обработки." : p.estimated ? "Поэтапная оценка, не прогноз времени. Процент может оставаться на одном значении, пока модель выполняет шаг." : "Прогресс по обработанному аудио. Загрузка модели и длинные фрагменты могут занимать время."} Можно открыть другую встречу.</p>` : ""}
  </section>`;
}


async function showVoices() {
  if (state.dirty) throw Error("Сначала сохраните изменения текста и протокола");
  const m = state.current;
  const result = await api(`/api/meetings/${m.id}/voices`);
  let dialog = $("#voices-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "voices-dialog";
    document.body.append(dialog);
  }
  dialog.innerHTML = `<div class="dialog-heading"><h2>Голосовые профили</h2><button class="icon-btn" id="voices-close" aria-label="Закрыть голосовые профили">×</button></div>
    <p>Сравнение тембра с сохранёнными образцами выполняется на этом компьютере. Сначала прослушайте голос и проверьте имя. Это подсказка, а не подтверждение личности.</p>
    <p class="small-note">Нужно не менее 10 секунд речи без наложения голосов. Порог сходства 0,80 и отрыв от второго кандидата 0,10 — предварительные, точность на независимых записях ещё не измерена. Имена не подставляются автоматически.</p>
    ${Object.keys(result.candidates).length ? Object.entries(result.candidates).map(([speaker,c]) => `<div class="voice-row"><strong>${esc(m.speakers[speaker] || speaker)}</strong><p class="meta">${esc(speaker)} · ${c.seconds} с чистой речи</p>${c.name ? `<p>Возможно, ${esc(c.name)} · сходство ${c.score}</p><button class="secondary" data-use-voice="${esc(speaker)}">Подставить имя в редактор</button>` : '<p class="small-note">Однозначное совпадение не найдено.</p>'}<label>Имя для нового профиля<input data-voice-name="${esc(speaker)}" maxlength="200" placeholder="Имя или согласованный псевдоним"></label><label class="check-label"><input type="checkbox" data-voice-consent="${esc(speaker)}">Участник разрешил сохранить голосовой профиль; я прослушал и проверил этот голос</label><button class="secondary" data-enroll-voice="${esc(speaker)}" ${c.seconds < 10 ? "disabled" : ""}>Сохранить голосовой профиль</button></div>`).join("") : '<p>Для этой встречи ещё нет голосовых образцов. Для старой записи запустите разделение голосов заново; повторная обработка сбросит протокол.</p>'}
    <h3>Сохранённые профили</h3><p class="small-note">Имена и голосовые признаки хранятся локально отдельно от встречи. Удаление встречи не удаляет эти профили. Здесь можно удалить профиль; уже подтверждённые имена в протоколах сохранятся.</p>
    ${result.profiles.map(p => `<div class="voice-profile"><span>${esc(p.name)}</span><button class="text-btn" data-delete-voice="${p.id}">Удалить профиль</button></div>`).join("") || '<p class="small-note">Профилей пока нет.</p>'}`;
  $("#voices-close").onclick = () => dialog.close();
  dialog.onclick = async e => {
    const button = e.target.closest("button");
    if (!button || button.id === "voices-close") return;
    button.disabled = true;
    try {
      if (button.dataset.useVoice) {
        const speaker = button.dataset.useVoice;
        const input = $$("[data-speaker-name]").find(el => el.dataset.speakerName === speaker);
        if (!input) throw Error("Этот голос отсутствует в транскрипте");
        input.value = result.candidates[speaker].name;
        input.dispatchEvent(new Event("input", {bubbles:true}));
        dialog.close();
        toast("Имя подставлено. Проверьте и нажмите «Сохранить текст»; протокол потребуется сформировать заново.");
      } else if (button.dataset.enrollVoice) {
        const speaker = button.dataset.enrollVoice;
        const name = $$('[data-voice-name]').find(el => el.dataset.voiceName === speaker).value;
        const consent = $$('[data-voice-consent]').find(el => el.dataset.voiceConsent === speaker).checked;
        await api(`/api/meetings/${m.id}/voices`, json("POST", {speaker, name, consent}));
        await showVoices();
        toast("Голосовой профиль сохранён локально");
      } else if (button.dataset.deleteVoice) {
        await api(`/api/voices/${button.dataset.deleteVoice}`, {method:"DELETE"});
        await showVoices();
        toast("Голосовой профиль удалён");
      }
    } catch (err) { toast(err.message, true); }
    finally { button.disabled = false; }
  };
  if (!dialog.open) dialog.showModal();
}
