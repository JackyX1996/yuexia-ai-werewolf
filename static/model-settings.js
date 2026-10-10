// Credentials stay in this form only; never store them in browser storage.
let modelConfigurations = [];
let editingModelId = null;
let modelFormBusy = false;
const modelProviderNames = {bailian: '百炼', volcengine: '火山方舟', openai: '自定义'};
const modelProviderUrls = {
    bailian: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    volcengine: 'https://ark.cn-beijing.volces.com/api/v3',
};
const modelFormatNames = {
    chat_completions: 'Chat Completions',
    responses: 'Responses',
    anthropic_messages: 'Anthropic Messages',
};
const modelFormatHelp = {
    chat_completions: {
        placeholder: 'https://api.openai.com/v1',
        help: '填写到版本根路径，例如 /v1。不要带 /chat/completions。',
        summary: '按 OpenAI Chat Completions 调用。',
    },
    responses: {
        placeholder: 'https://api.openai.com/v1',
        help: '填写到版本根路径，例如 /v1。请求会发往 /responses。',
        summary: '按 OpenAI Responses 调用。',
    },
    anthropic_messages: {
        placeholder: 'https://api.anthropic.com',
        help: '填写服务根地址。请求会发往 /v1/messages，并带上 x-api-key。',
        summary: '按 Anthropic Messages 调用。',
    },
};

async function showModelSettings() {
    show('model-settings-screen');
    closeModelEditor();
    $('#model-page-status').textContent = '';
    $('#model-configuration-list').textContent = '正在加载配置…';
    await loadModelConfigurations();
}

async function loadModelConfigurations() {
    try {
        modelConfigurations = await (await apiFetch('/api/model-configurations')).json();
        renderModelConfigurations();
        return true;
    } catch (error) {
        $('#model-configuration-list').textContent = error.message;
        return false;
    }
}

function renderModelConfigurations() {
    const list = $('#model-configuration-list');
    list.replaceChildren();
    const allModels = modelConfigurations.flatMap(item => item.models);
    const enabledCount = allModels.filter(item => item.enabled).length;
    $('#model-settings-summary').textContent = modelConfigurations.length
        ? `${modelConfigurations.length} 个平台 · ${allModels.length} 个模型 · ${enabledCount} 个可用`
        : '';
    if (!modelConfigurations.length) {
        list.innerHTML = '<div class="empty-state">还没有平台。添加百炼、火山方舟，或一个自定义接口。</div>';
        return;
    }
    for (const item of modelConfigurations) {
        const card = document.createElement('article');
        card.className = 'model-configuration-card';
        card.tabIndex = 0;
        card.setAttribute('role', 'button');
        card.setAttribute('aria-label', `编辑${modelProviderNames[item.provider]}`);
        const heading = document.createElement('h3');
        const format = item.provider === 'openai' ? ` · ${modelFormatNames[item.api_format] || ''}` : '';
        heading.textContent = `${modelProviderNames[item.provider] || item.provider}${format}`;
        const details = document.createElement('p');
        details.className = 'muted';
        details.textContent = `${item.enabled ? '已加入对局池' : '未加入对局池'} · ${item.models.filter(model => model.enabled).length} 个可用`;
        const endpoint = document.createElement('p');
        endpoint.className = 'muted model-endpoint';
        endpoint.textContent = item.base_url;
        const chips = document.createElement('div');
        chips.className = 'model-chips';
        const visible = item.models.slice(0, 8);
        for (const model of visible) {
            const chip = document.createElement('span');
            chip.className = 'model-chip' + (model.enabled ? '' : ' off');
            chip.textContent = model.model_name;
            chips.append(chip);
        }
        if (item.models.length > visible.length) {
            const more = document.createElement('span');
            more.className = 'model-chip';
            more.textContent = `+${item.models.length - visible.length}`;
            chips.append(more);
        }
        const actions = document.createElement('div');
        actions.className = 'button-row';
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.textContent = '编辑';
        edit.disabled = modelFormBusy;
        edit.onclick = event => { event.stopPropagation(); editModelConfiguration(item.id); };
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'quiet danger';
        remove.textContent = '删除';
        remove.disabled = modelFormBusy;
        remove.onclick = event => { event.stopPropagation(); askDeleteModelConfiguration(item.id, actions); };
        actions.append(edit, remove);
        card.append(heading, details, endpoint, chips, actions);
        const open = () => editModelConfiguration(item.id);
        card.onclick = open;
        card.onkeydown = event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); }
        };
        list.append(card);
    }
}

function askDeleteModelConfiguration(id, actions) {
    const item = modelConfigurations.find(configuration => configuration.id === id);
    if (modelFormBusy || !item || actions.parentElement.querySelector('.model-delete-confirm')) return;
    const box = document.createElement('div');
    box.className = 'model-delete-confirm';
    box.textContent = `删除这 ${item.models.length} 个模型？`;
    const yes = document.createElement('button');
    yes.type = 'button';
    yes.className = 'quiet danger';
    yes.textContent = '确认删除';
    yes.onclick = event => { event.stopPropagation(); deleteModelConfiguration(id); };
    const no = document.createElement('button');
    no.type = 'button';
    no.className = 'quiet';
    no.textContent = '取消';
    no.onclick = event => { event.stopPropagation(); box.replaceWith(actions); };
    box.append(yes, no);
    actions.replaceWith(box);
}

function setChoice(group, value) {
    group.querySelectorAll('button').forEach(button => {
        const selected = button.dataset.value === value;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
}

function openModelEditor() {
    $('#model-editor-overlay').classList.remove('hidden');
    document.addEventListener('keydown', onModelEditorKey);
    $('#model-names').focus({preventScroll: true});
}

function closeModelEditor() {
    if (modelFormBusy) return;
    const overlay = $('#model-editor-overlay');
    if (!overlay || overlay.classList.contains('hidden')) return;
    overlay.classList.add('hidden');
    $('#model-api-key').value = '';
    document.removeEventListener('keydown', onModelEditorKey);
}

function onModelEditorKey(event) {
    if (event.key === 'Escape') closeModelEditor();
}

function newModelConfiguration() {
    if (modelFormBusy) return;
    editingModelId = null;
    $('#model-configuration-form').reset();
    $('#model-provider').value = 'bailian';
    $('#model-api-format').value = 'chat_completions';
    $('#model-editor-title').textContent = '添加平台';
    $('#model-form-status').textContent = '';
    $('#model-form-status').className = 'model-form-status';
    setChoice($('#model-provider-choices'), 'bailian');
    setChoice($('#model-format-choices'), 'chat_completions');
    updateModelProvider();
    updateTestModels();
    openModelEditor();
}

function editModelConfiguration(id) {
    if (modelFormBusy) return;
    const item = modelConfigurations.find(configuration => configuration.id === id);
    if (!item) return;
    editingModelId = id;
    $('#model-editor-title').textContent = '编辑平台';
    $('#model-provider').value = item.provider;
    $('#model-api-format').value = item.api_format || 'chat_completions';
    $('#model-names').value = item.model_names.join('\n');
    $('#model-base-url').value = item.base_url;
    $('#model-api-key').value = '';
    $('#model-enabled').checked = item.enabled;
    $('#model-thinking-format').value = item.thinking_format || 'auto';
    $('#model-form-status').textContent = '';
    $('#model-form-status').className = 'model-form-status';
    setChoice($('#model-provider-choices'), item.provider);
    setChoice($('#model-format-choices'), $('#model-api-format').value);
    updateModelProvider();
    updateTestModels();
    const firstTestable = item.models.find(model => !model.unavailable_reason);
    if (firstTestable) $('#model-test-name').value = firstTestable.model_name;
    openModelEditor();
}

function updateModelProvider() {
    const provider = $('#model-provider').value;
    const custom = provider === 'openai';
    const format = custom ? $('#model-api-format').value : 'chat_completions';
    const help = modelFormatHelp[format] || modelFormatHelp.chat_completions;
    const existing = modelConfigurations.find(item => item.id === editingModelId);
    $('#model-format-field').classList.toggle('hidden', !custom);
    $('#model-base-url-field').classList.toggle('hidden', !custom);
    $('#model-thinking-format-field').classList.toggle('hidden', !(custom && format === 'chat_completions'));
    $('#model-base-url').required = custom;
    $('#model-base-url').placeholder = help.placeholder;
    $('#model-base-url-help').textContent = help.help;
    $('#model-format-hint').textContent = help.summary;
    const expectedUrl = custom ? $('#model-base-url').value.trim().replace(/\/+$/, '') : modelProviderUrls[provider];
    const canKeepKey = existing && existing.has_api_key && existing.provider === provider &&
        (existing.api_format || 'chat_completions') === format && existing.base_url === expectedUrl;
    $('#model-api-key').required = !canKeepKey;
    $('#model-api-key').placeholder = canKeepKey ? existing.api_key_masked || '已保存密钥' : '填写 API Key';
    $('#model-api-key').classList.toggle('has-saved-key', !!canKeepKey);
    $('#model-key-hint').textContent = canKeepKey ? '已保存。留空会保留原密钥，填写则替换。' : '密钥只保存在服务端。';
    $('#model-endpoint-hint').textContent = custom ? '填写你自己的服务地址，并选择它使用的接口。' : `使用官方地址：${modelProviderUrls[provider]}`;
}

function modelNamesFromForm() {
    return [...new Set($('#model-names').value.split(/\r?\n/).map(name => name.trim()).filter(Boolean))];
}

function updateTestModels() {
    const select = $('#model-test-name');
    const selected = select.value;
    select.replaceChildren();
    const names = modelNamesFromForm();
    for (const name of names.length ? names : ['']) {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name || '请先填写模型名';
        select.append(option);
    }
    if (names.includes(selected)) select.value = selected;
    const existing = modelConfigurations.find(item => item.id === editingModelId);
    const unavailable = existing ? existing.models.filter(model => model.unavailable_reason && names.includes(model.model_name)) : [];
    $('#model-unavailable-list').textContent = unavailable.length
        ? `这些模型关不掉 Thinking，不会参与开局：${unavailable.map(model => model.model_name).join('、')}` : '';
    $('#model-unavailable-list').classList.toggle('hidden', !unavailable.length);
}

function modelConfigurationPayload() {
    const form = $('#model-configuration-form');
    if (!form.reportValidity()) return null;
    const names = modelNamesFromForm();
    if (!names.length || names.some(name => /\s/.test(name) || name.length > 100)) {
        $('#model-form-status').className = 'model-form-status error';
        $('#model-form-status').textContent = '每行一个模型名，不超过 100 个字符，不能包含空格。';
        return null;
    }
    const provider = $('#model-provider').value;
    const key = $('#model-api-key').value.trim();
    return {
        provider,
        model_names: names,
        api_key: key || null,
        base_url: provider === 'openai' ? $('#model-base-url').value.trim() : '',
        enabled: $('#model-enabled').checked,
        thinking_format: $('#model-thinking-format').value,
        api_format: provider === 'openai' ? $('#model-api-format').value : 'chat_completions',
    };
}

function setModelFormBusy(busy) {
    modelFormBusy = busy;
    $('#model-configuration-form').querySelectorAll('input, textarea, select, button').forEach(element => { element.disabled = busy; });
    $('#btn-close-model-editor').disabled = busy;
    $('#btn-new-model').disabled = busy;
    renderModelConfigurations();
}

async function saveModelConfiguration(event) {
    event.preventDefault();
    if (modelFormBusy) return;
    const payload = modelConfigurationPayload();
    if (!payload) return;
    setModelFormBusy(true);
    const status = $('#model-form-status');
    status.className = 'model-form-status';
    status.textContent = '正在保存…';
    try {
        const url = '/api/model-configurations' + (editingModelId ? `/${encodeURIComponent(editingModelId)}` : '');
        const saved = await (await apiFetch(url, {method: editingModelId ? 'PUT' : 'POST', body: JSON.stringify(payload)})).json();
        editingModelId = saved.id;
        $('#model-api-key').value = '';
        $('#model-editor-title').textContent = '编辑平台';
        await loadModelConfigurations();
        updateModelProvider();
        updateTestModels();
        status.className = 'model-form-status success';
        status.textContent = '已保存，现在就可以测试连通。';
    } catch (error) {
        status.className = 'model-form-status error';
        status.textContent = error.message;
    } finally {
        setModelFormBusy(false);
    }
}

async function testModelConfiguration() {
    if (modelFormBusy) return;
    const payload = modelConfigurationPayload();
    if (!payload) return;
    if (!$('#model-test-name').value) {
        $('#model-form-status').className = 'model-form-status error';
        $('#model-form-status').textContent = '先写一个模型名，再测试。';
        return;
    }
    setModelFormBusy(true);
    const status = $('#model-form-status');
    status.className = 'model-form-status';
    status.textContent = `正在测试 ${$('#model-test-name').value}，最长 20 秒。这一步不会保存。`;
    $('#btn-test-model').textContent = '测试中…';
    try {
        const result = await (await apiFetch('/api/model-configurations/test', {
            method: 'POST', body: JSON.stringify({...payload, id: editingModelId, model_name: $('#model-test-name').value}),
        })).json();
        status.className = 'model-form-status ' + (result.success ? 'success' : 'error');
        status.textContent = `${result.model_name}：${result.message} 耗时 ${(result.elapsed_ms / 1000).toFixed(2)} 秒。测试不会保存。`;
    } catch (error) {
        status.className = 'model-form-status error';
        status.textContent = error.message;
    } finally {
        $('#btn-test-model').textContent = '测试连通';
        setModelFormBusy(false);
    }
}

async function deleteModelConfiguration(id) {
    if (modelFormBusy) return;
    setModelFormBusy(true);
    $('#model-page-status').className = 'model-form-status';
    $('#model-page-status').textContent = '';
    try {
        await apiFetch(`/api/model-configurations/${encodeURIComponent(id)}`, {method: 'DELETE'});
        if (editingModelId === id) {
            modelFormBusy = false;
            closeModelEditor();
            editingModelId = null;
        }
        await loadModelConfigurations();
    } catch (error) {
        $('#model-page-status').className = 'model-form-status error';
        $('#model-page-status').textContent = error.message;
        await loadModelConfigurations();
    } finally {
        setModelFormBusy(false);
    }
}

function leaveModelSettings() {
    if (modelFormBusy) return;
    closeModelEditor();
    goHome();
}

function chooseModelOption(event, inputId) {
    const button = event.target.closest('button');
    if (!button || modelFormBusy) return;
    $(inputId).value = button.dataset.value;
    setChoice(button.parentElement, button.dataset.value);
    if (inputId === '#model-provider') $('#model-api-key').value = '';
    updateModelProvider();
    $('#model-form-status').textContent = '';
}

$('#model-provider-choices').addEventListener('click', event => chooseModelOption(event, '#model-provider'));
$('#model-format-choices').addEventListener('click', event => chooseModelOption(event, '#model-api-format'));
$('#model-base-url').addEventListener('input', updateModelProvider);
$('#model-names').addEventListener('input', updateTestModels);
$('#model-configuration-form').addEventListener('submit', saveModelConfiguration);
$('#model-configuration-form').addEventListener('input', event => {
    if (event.target.id !== 'model-base-url') $('#model-form-status').textContent = '';
});
