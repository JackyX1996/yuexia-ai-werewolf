// Keep the lobby button aligned with the server's enabled model pool.
let _canCreateGame = false;
let _creatingGame = false;
let _modelAvailabilityRequest = 0;

async function refreshGameAvailability() {
    const requestId = ++_modelAvailabilityRequest;
    const button = $('#btn-create');
    const hint = $('#game-model-hint');
    const configure = $('#btn-configure-models');
    const retry = $('#btn-retry-models');
    _canCreateGame = false;
    button.disabled = true;
    button.title = '正在检查模型配置';
    hint.textContent = '正在检查模型配置…';
    configure.classList.add('hidden');
    retry.classList.add('hidden');
    try {
        const response = await apiFetch('/api/model-configurations/status');
        const availability = await response.json();
        // A late response must not overwrite a more recent configuration check.
        if (requestId !== _modelAvailabilityRequest) return;
        _canCreateGame = availability.can_start === true;
        button.disabled = !_canCreateGame || _creatingGame;
        button.title = _canCreateGame ? '' : '请先配置并启用至少 1 个模型';
        if (availability.ai_mode === 'simulated') {
            hint.textContent = '模拟预览已就绪，无需配置真实模型。';
        } else if (_canCreateGame) {
            hint.textContent = `已有 ${availability.available_count} 个可用模型，开局随机选取 11 个，不足时重复分配。`;
        } else {
            const admin = isAdminUser();
            hint.textContent = admin ? '尚无可用模型，请先配置并启用至少 1 个模型。' : '尚无可用模型，请联系管理员配置并启用至少 1 个模型。';
            configure.classList.toggle('hidden', !admin);
        }
    } catch (_) {
        if (requestId !== _modelAvailabilityRequest) return;
        _canCreateGame = false;
        button.disabled = true;
        button.title = '无法检查模型配置，请重试';
        hint.textContent = '无法检查模型配置，请重试。';
        retry.classList.remove('hidden');
    }
}
