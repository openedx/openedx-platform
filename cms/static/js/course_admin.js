/* global fetch */
(function() {
    'use strict';

    var root = document.getElementById('course-admin');
    if (!root) {
        return;
    }

    var settingsForm = document.getElementById('course-settings');
    var courseCheckboxes = Array.from(root.querySelectorAll('input[name="course_ids"]'));
    var selectPage = document.getElementById('select-page');
    var allMatching = document.getElementById('all-matching');
    var previewButton = document.getElementById('preview-rerun');
    var rerunButton = document.getElementById('rerun-courses');
    var saveButton = document.getElementById('save-settings');
    var actionMessage = document.getElementById('action-message');
    var pollMessage = document.getElementById('poll-message');
    var resultBody = document.getElementById('operation-results');
    var results = new Map();
    var appliedFilters = JSON.parse(root.dataset.filters);
    var total = Number(root.dataset.total);
    var busy = false;
    var previewSignature = null;
    var previewTargets = null;
    var previewToken = null;
    var pollTimer = null;
    var polling = false;
    var pollDelay = 3000;
    var stateLabels = {
        preview: 'Проверено',
        queued: 'В очереди',
        in_progress: 'Создаётся',
        succeeded: 'Готово',
        failed: 'Ошибка',
        saved: 'Сохранено'
    };

    function selectedIds() {
        return courseCheckboxes.filter(function(input) { return input.checked; })
            .map(function(input) { return input.value; });
    }

    function selectedCount() {
        return allMatching.checked ? total : selectedIds().length;
    }

    function notify(message, kind) {
        actionMessage.textContent = message;
        actionMessage.className = 'notice' + (kind ? ' ' + kind : '');
        actionMessage.hidden = !message;
    }

    function updateControls() {
        var count = selectedCount();
        var selectedOnPage = selectedIds().length;
        document.getElementById('selection-count').textContent = 'Выбрано: ' + count;
        selectPage.checked = allMatching.checked || (courseCheckboxes.length > 0 && selectedOnPage === courseCheckboxes.length);
        selectPage.indeterminate = !allMatching.checked && selectedOnPage > 0 && selectedOnPage < courseCheckboxes.length;
        selectPage.disabled = busy || allMatching.checked || !courseCheckboxes.length;
        allMatching.disabled = busy || !total;
        courseCheckboxes.forEach(function(input) { input.disabled = busy || allMatching.checked; });
        previewButton.disabled = busy || !count;
        saveButton.disabled = busy || !count;
        rerunButton.disabled = busy || !count || !previewSignature;
        Array.from(settingsForm.elements).forEach(function(input) {
            if (input.tagName !== 'BUTTON' && input.type !== 'hidden') {
                var clearInput = settingsForm.elements.namedItem('clear_' + input.name);
                input.disabled = busy || Boolean(clearInput && clearInput.checked);
            }
        });
        root.setAttribute('aria-busy', busy ? 'true' : 'false');
    }

    function invalidatePreview() {
        previewSignature = null;
        previewTargets = null;
        previewToken = null;
        document.getElementById('preview-hint').textContent = 'Сначала выберите курсы и проверьте новые ID. После изменения настроек проверку нужно повторить.';
        updateControls();
    }

    function collectPayload(action) {
        var settings = {};
        ['start', 'end', 'enrollment_start', 'enrollment_end', 'certificate_available_date'].forEach(function(name) {
            var input = settingsForm.elements.namedItem(name);
            var clearInput = settingsForm.elements.namedItem('clear_' + name);
            if (clearInput && clearInput.checked) {
                settings[name] = null;
            } else if (input.value) {
                // datetime-local has no zone. This form explicitly collects UTC, independent of the browser zone.
                var date = new Date(input.value + 'Z');
                if (Number.isNaN(date.getTime())) {
                    throw new Error('Проверьте дату: ' + name + '.');
                }
                settings[name] = date.toISOString();
            }
        });
        ['catalog_visibility', 'mode', 'certificate_mode', 'self_paced', 'invitation_only'].forEach(function(name) {
            var value = settingsForm.elements.namedItem(name).value;
            if (value !== '') {
                settings[name] = name === 'self_paced' || name === 'invitation_only' ? value === 'true' : value;
            }
        });
        if (action !== 'save') {
            if (!settings.start || !settings.end) {
                throw new Error('Для нового запуска заполните начало и конец курса в UTC.');
            }
            settings.mode = settings.mode || 'copy';
            settings.certificate_mode = settings.certificate_mode || 'copy';
            settings.shift_content_dates = settingsForm.elements.namedItem('shift_content_dates').checked;
        } else if (!Object.keys(settings).length) {
            throw new Error('Заполните хотя бы одно поле или выберите настройку для изменения.');
        }
        if (settings.start && settings.end && settings.start >= settings.end) {
            throw new Error('Конец курса должен быть позже начала.');
        }
        if (settings.enrollment_start && settings.enrollment_end && settings.enrollment_start >= settings.enrollment_end) {
            throw new Error('Конец записи должен быть позже начала записи.');
        }
        if (!selectedCount()) {
            throw new Error('Выберите хотя бы один курс.');
        }
        return {
            action: action,
            course_ids: allMatching.checked ? [] : selectedIds(),
            all_matching: allMatching.checked,
            filters: appliedFilters,
            settings: settings
        };
    }

    function signature(payload) {
        return JSON.stringify({
            course_ids: payload.course_ids,
            all_matching: payload.all_matching,
            filters: payload.filters,
            settings: payload.settings
        });
    }

    async function readResponse(response) {
        var body;
        try {
            body = await response.json();
        } catch (error) {
            throw new Error('Сервер вернул неожиданный ответ. Проверьте авторизацию и обновите страницу.');
        }
        if (!response.ok) {
            var detail = body.error || body.message;
            if (typeof detail !== 'string') {
                detail = 'Запрос не выполнен (HTTP ' + response.status + ').';
            }
            var requestError = new Error(detail);
            requestError.status = response.status;
            requestError.results = body.results;
            throw requestError;
        }
        if (!Array.isArray(body.results)) {
            throw new Error('В ответе сервера отсутствует список результатов. Обновите страницу для проверки операций.');
        }
        return body;
    }

    function renderResults() {
        resultBody.replaceChildren();
        var counts = {};
        results.forEach(function(result) {
            var row = document.createElement('tr');
            [result.source_course_id, result.course_id].forEach(function(id) {
                var cell = document.createElement('td');
                cell.className = 'result-id';
                var code = document.createElement('code');
                code.textContent = id || '—';
                cell.appendChild(code);
                row.appendChild(cell);
            });
            var stateCell = document.createElement('td');
            var state = document.createElement('span');
            var knownState = Object.prototype.hasOwnProperty.call(stateLabels, result.state);
            state.className = 'status' + (knownState ? ' status-' + result.state : '');
            state.textContent = knownState ? stateLabels[result.state] : (result.state || 'Неизвестно');
            stateCell.appendChild(state);
            row.appendChild(stateCell);
            var message = document.createElement('td');
            message.className = 'result-message';
            message.textContent = result.message || '';
            row.appendChild(message);
            resultBody.appendChild(row);
            counts[result.state] = (counts[result.state] || 0) + 1;
        });
        document.getElementById('results-panel').hidden = !results.size;
        document.getElementById('results-summary').textContent = Object.keys(stateLabels)
            .filter(function(state) { return counts[state]; })
            .map(function(state) { return stateLabels[state] + ': ' + counts[state]; }).join(' · ');
    }

    function mergeResults(newResults, isPreview) {
        if (isPreview) {
            results.forEach(function(result, key) {
                if (result.state === 'preview') {
                    results.delete(key);
                }
            });
        }
        (newResults || []).forEach(function(result) {
            // A status lookup may return an unknown destination without its source.
            // Reuse the source from the pending row so the update replaces it and
            // polling does not continue forever under a second map key.
            if (!result.source_course_id && result.course_id) {
                results.forEach(function(existing) {
                    if (!result.source_course_id && existing.course_id === result.course_id) {
                        result = Object.assign({}, result, {source_course_id: existing.source_course_id});
                    }
                });
            }
            if (!Object.prototype.hasOwnProperty.call(stateLabels, result.state)) {
                result = Object.assign({}, result, {
                    state: 'failed',
                    message: result.message || 'Состояние операции недоступно. Проверьте курс в Studio.'
                });
            }
            var key = (result.source_course_id || '') + '→' + (result.course_id || '');
            results.set(key, result);
        });
        renderResults();
    }

    function pendingIds() {
        var ids = [];
        results.forEach(function(result) {
            if ((result.state === 'queued' || result.state === 'in_progress') && result.course_id) {
                ids.push(result.course_id);
            }
        });
        return Array.from(new Set(ids));
    }

    function schedulePoll() {
        if (!pollTimer && !polling && pendingIds().length) {
            pollTimer = window.setTimeout(poll, pollDelay);
        }
    }

    async function poll() {
        pollTimer = null;
        var ids = pendingIds();
        if (!ids.length) {
            return;
        }
        polling = true;
        try {
            // Keep status URLs bounded when the administrator selected all matching courses.
            for (var offset = 0; offset < ids.length; offset += 20) {
                var url = new URL(root.dataset.statusUrl, window.location.href);
                url.searchParams.set('course_ids', ids.slice(offset, offset + 20).join(','));
                var response = await fetch(url.toString(), {
                    credentials: 'same-origin',
                    cache: 'no-store',
                    headers: {Accept: 'application/json'}
                });
                var body = await readResponse(response);
                mergeResults(body.results, false);
            }
            pollMessage.hidden = true;
            pollDelay = 3000;
        } catch (error) {
            pollMessage.textContent = 'Не удалось обновить состояние: ' + error.message + ' Проверка будет повторена автоматически.';
            pollMessage.hidden = false;
            pollDelay = Math.min(pollDelay * 2, 30000);
        } finally {
            polling = false;
            schedulePoll();
        }
    }

    async function submitAction(action) {
        if (busy || !settingsForm.reportValidity()) {
            return;
        }
        var payload;
        try {
            payload = collectPayload(action);
            if (action === 'rerun' && previewSignature !== signature(payload)) {
                throw new Error('Настройки изменились. Сначала повторите проверку новых запусков.');
            }
            if (action === 'rerun') {
                payload.preview_targets = previewTargets;
                payload.preview_token = previewToken;
            }
        } catch (error) {
            notify(error.message, 'error');
            return;
        }
        busy = true;
        updateControls();
        notify(action === 'preview' ? 'Проверяем курсы и новые номера запусков…' : 'Отправляем операцию. Дождитесь ответа сервера…');
        try {
            var response = await fetch(root.dataset.endpoint, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    Accept: 'application/json',
                    'X-CSRFToken': settingsForm.elements.namedItem('csrfmiddlewaretoken').value
                },
                body: JSON.stringify(payload)
            });
            var body = await readResponse(response);
            mergeResults(body.results, action === 'preview');
            var failed = body.results.filter(function(result) { return result.state === 'failed'; }).length;
            if (action === 'preview') {
                var previewReady = Boolean(body.preview_token) && body.results.length > 0 && body.results.every(function(result) {
                    return result.state === 'preview' && result.source_course_id && result.course_id;
                });
                previewSignature = previewReady ? signature(payload) : null;
                previewToken = previewReady ? body.preview_token : null;
                previewTargets = previewReady ? {} : null;
                if (previewReady) {
                    body.results.forEach(function(result) { previewTargets[result.source_course_id] = result.course_id; });
                }
                document.getElementById('preview-hint').textContent = previewReady
                    ? 'Проверено запусков: ' + body.results.length + '. Новые ID показаны ниже. Нажмите «Создать новые запуски», чтобы начать.'
                    : 'Проверьте сообщения по каждому курсу и повторите проверку после исправления ошибок.';
                notify(previewReady ? 'Предварительная проверка завершена. Новые курсы ещё не созданы.' : 'Не все курсы прошли проверку. Подробности ниже.', previewReady ? 'success' : 'error');
            } else {
                invalidatePreview();
                notify(failed
                    ? 'Операция завершилась с ошибками для ' + failed + ' курсов. Проверьте результаты ниже.'
                    : (action === 'rerun' ? 'Запрос принят. Следите за состоянием создания курсов ниже.' : 'Настройки сохранены. Результаты по каждому курсу показаны ниже.'), failed ? 'error' : 'success');
            }
            schedulePoll();
        } catch (error) {
            var safeRerunRetry = action === 'rerun' && (!error.status || error.status >= 500);
            if (!safeRerunRetry) {
                invalidatePreview();
            }
            if (Array.isArray(error.results)) {
                mergeResults(error.results, action === 'preview');
                schedulePoll();
            }
            var message = error.message;
            if (safeRerunRetry) {
                message += ' Можно повторить создание: сохранены те же ID и параметры, чтобы не создать дубликаты.';
            } else if (action !== 'preview') {
                message += ' Если запрос успел выполниться, изменения уже применены. Перед повтором обновите страницу и проверьте результаты.';
            }
            notify(message, 'error');
        } finally {
            busy = false;
            updateControls();
        }
    }

    selectPage.addEventListener('change', function() {
        courseCheckboxes.forEach(function(input) { input.checked = selectPage.checked; });
        invalidatePreview();
    });
    allMatching.addEventListener('change', invalidatePreview);
    courseCheckboxes.forEach(function(input) { input.addEventListener('change', invalidatePreview); });
    settingsForm.addEventListener('input', invalidatePreview);
    settingsForm.addEventListener('change', invalidatePreview);
    document.getElementById('course-filters').addEventListener('input', invalidatePreview);
    document.getElementById('course-filters').addEventListener('change', invalidatePreview);
    settingsForm.addEventListener('submit', function(event) { event.preventDefault(); });
    previewButton.addEventListener('click', function() { submitAction('preview'); });
    rerunButton.addEventListener('click', function() { submitAction('rerun'); });
    saveButton.addEventListener('click', function() { submitAction('save'); });

    mergeResults(JSON.parse(root.dataset.initialResults), false);
    updateControls();
    schedulePoll();
}());
