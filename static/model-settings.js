// Credentials stay in this form only; never store them in browser storage.
let modelConfigurations = [];
let editingModelId = null;
let modelFormBusy = false;
const modelProviderNames = {bailian: '百炼', volcengine: '火山方舟', openai: 'OpenAI 兼容'};
const modelProviderUrls = {
    bailian: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    volcengine: 'https://ark.cn-beijing.volces.com/api/v3',
};

async function showModelSettings() {
    show('model-settings-screen');
    newModelConfiguration();
    $('#model-configuration-list').textContent = '正在加载配置…';
    if (await loadModelConfigurations() && modelConfigurations.length) {
        editModelConfiguration(modelConfigurations[0].id, false);
    }
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
    $('#model-settings-summary').textContent = `${modelConfigurations.length} 个平台配置 · ${allModels.length} 个模型 · ${enabledCount} 个可用`;
    if (!modelConfigurations.length) {
        list.innerHTML = '<div class="empty-state">还没有平台配置。填写地址、密钥和模型列表，保存后即可开始对局。</div>';
        return;
    }
    for (const item of modelConfigurations) {
        const card = document.createElement('article');
        card.className = 'model-configuration-card' + (item.id === editingModelId ? ' selected' : '');
        const heading = document.createElement('h3');
        heading.textContent = `${modelProviderNames[item.provider]} · ${item.models.length} 个模型`;
        const details = document.createElement('p');
        details.className = 'muted';
        details.textContent = `${item.enabled ? '已启用' : '已停用'} · ${item.models.filter(model => model.enabled).length} 个可用`;
        const endpoint = document.createElement('p');
        endpoint.className = 'muted model-endpoint';
        endpoint.textContent = item.base_url;
        const actions = document.createElement('div');
        actions.className = 'button-row';
        const edit = document.createElement('button');
        edit.textContent = '编辑 / 测试';
        edit.disabled = modelFormBusy;
        edit.onclick = () => editModelConfiguration(item.id);
        const remove = document.createElement('button');
        remove.className = 'quiet danger';
        remove.textContent = '删除';
        remove.disabled = modelFormBusy;
        remove.onclick = () => deleteModelConfiguration(item.id);
        actions.append(edit, remove);
        card.append(heading, details, endpoint, actions);
        list.append(card);
    }
}

function newModelConfiguration() {
    if (modelFormBusy) return;
    editingModelId = null;
    $('#model-configuration-form').reset();
    $('#model-editor-title').textContent = '添加平台';
    $('#model-form-status').textContent = '';
    updateModelProvider();
    updateTestModels();
    renderModelConfigurations();
}

function editModelConfiguration(id, focusEditor = true) {
    if (modelFormBusy) return;
    const item = modelConfigurations.find(configuration => configuration.id === id);
    if (!item) return;
    editingModelId = id;
    $('#model-editor-title').textContent = '编辑平台';
    $('#model-provider').value = item.provider;
    $('#model-names').value = item.model_names.join('\n');
    $('#model-base-url').value = item.base_url;
    $('#model-api-key').value = '';
    $('#model-enabled').checked = item.enabled;
    $('#model-thinking-format').value = item.thinking_format;
    $('#model-form-status').textContent = '';
    updateModelProvider();
    renderModelConfigurations();
    updateTestModels();
    const firstTestable = item.models.find(model => !model.unavailable_reason);
    if (firstTestable) $('#model-test-name').value = firstTestable.model_name;
    if (focusEditor) {
        $('#model-names').focus({preventScroll: true});
        $('.model-settings-editor').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }
}

function updateModelProvider() {
    const provider = $('#model-provider').value;
    const compatible = provider === 'openai';
    const existing = modelConfigurations.find(item => item.id === editingModelId);
    $('#model-base-url-field').classList.toggle('hidden', !compatible);
    $('#model-thinking-format-field').classList.toggle('hidden', !compatible);
    $('#model-base-url').required = compatible;
    const canKeepKey = existing && existing.has_api_key && existing.provider === provider &&
        existing.base_url === (compatible ? $('#model-base-url').value.trim().replace(/\/+$/, '') : modelProviderUrls[provider]);
    $('#model-api-key').required = !canKeepKey;
    $('#model-api-key').placeholder = canKeepKey ? existing.api_key_masked || '已保存密钥' : '填写 API Key';
    $('#model-api-key').classList.toggle('has-saved-key', !!canKeepKey);
    $('#model-key-hint').textContent = canKeepKey ? '密钥已保存；留空保留，填写新 Key 则替换。' : '密钥保存于服务端。';
    $('#model-endpoint-hint').textContent = compatible ? '使用自定义服务地址，支持 OpenAI Chat Completions 接口。' : `官方服务地址：${modelProviderUrls[provider]}`;
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
        ? `以下模型不可用，不参与开局：${unavailable.map(model => model.model_name).join('、')}` : '';
    $('#model-unavailable-list').classList.toggle('hidden', !unavailable.length);
}

function modelConfigurationPayload() {
    const form = $('#model-configuration-form');
    if (!form.reportValidity()) return null;
    const names = modelNamesFromForm();
    if (!names.length || names.some(name => /\s/.test(name) || name.length > 100)) {
        $('#model-form-status').className = 'model-form-status error';
        $('#model-form-status').textContent = '每行填写一个模型名，长度不超过 100 个字符，不能包含空白字符。';
        return null;
    }
    const key = $('#model-api-key').value.trim();
    return {
        provider: $('#model-provider').value,
        model_names: names,
        api_key: key || null,
        base_url: $('#model-provider').value === 'openai' ? $('#model-base-url').value.trim() : '',
        enabled: $('#model-enabled').checked,
        thinking_format: $('#model-thinking-format').value,
    };
}

function setModelFormBusy(busy) {
    modelFormBusy = busy;
    $('#model-configuration-form').querySelectorAll('input, textarea, select, button').forEach(element => { element.disabled = busy; });
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
        status.textContent = '已保存，配置立即生效。';
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
    setModelFormBusy(true);
    const status = $('#model-form-status');
    status.className = 'model-form-status';
    status.textContent = '正在发送真实测试请求（最长 20 秒）…';
    $('#btn-test-model').textContent = '测试中…';
    try {
        const result = await (await apiFetch('/api/model-configurations/test', {
            method: 'POST', body: JSON.stringify({...payload, id: editingModelId, model_name: $('#model-test-name').value}),
        })).json();
        status.className = 'model-form-status ' + (result.success ? 'success' : 'error');
        status.textContent = `${result.model_name}：${result.message} 耗时 ${(result.elapsed_ms / 1000).toFixed(2)} 秒。测试不会保存配置。`;
    } catch (error) {
        status.className = 'model-form-status error';
        status.textContent = error.message;
    } finally {
        $('#btn-test-model').textContent = '测试连通';
        setModelFormBusy(false);
    }
}

async function deleteModelConfiguration(id) {
    const item = modelConfigurations.find(configuration => configuration.id === id);
    if (modelFormBusy || !item || !confirm(`删除此平台及其 ${item.models.length} 个模型？`)) return;
    setModelFormBusy(true);
    try {
        await apiFetch(`/api/model-configurations/${encodeURIComponent(id)}`, {method: 'DELETE'});
        await loadModelConfigurations();
        if (editingModelId === id) {
            setModelFormBusy(false);
            newModelConfiguration();
        }
    } catch (error) {
        $('#model-form-status').className = 'model-form-status error';
        $('#model-form-status').textContent = error.message;
    } finally {
        setModelFormBusy(false);
    }
}

function leaveModelSettings() {
    if (modelFormBusy) return;
    $('#model-api-key').value = '';
    goHome();
}

$('#model-provider').addEventListener('change', () => {
    $('#model-api-key').value = '';
    updateModelProvider();
    $('#model-form-status').textContent = '';
});
$('#model-base-url').addEventListener('input', updateModelProvider);
$('#model-names').addEventListener('input', updateTestModels);
$('#model-configuration-form').addEventListener('submit', saveModelConfiguration);
$('#model-configuration-form').addEventListener('input', event => {
    if (event.target.id !== 'model-base-url') $('#model-form-status').textContent = '';
});
