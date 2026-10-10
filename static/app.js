// ── State ──
let gameId = null;
let ws = null;
let myRole = null;
let mySeat = null;
let godMode = false;
let players = [];
let sheriffSeat = null;
let _speakingTimer = null;
let _gameTimerInterval = null;
let _gameStartedAt = null;
let _currentRound = 0;
let _currentPhase = 'created';
let _currentSpeaker = null;
let _speechOrder = [];
let _speechRecords = [];
let _pendingPrompt = null;
let _sendingAction = false;
let _selectedAction = null;
let _wsGeneration = 0;
let _reconnectTimer = null;
let _actionClock = null;
let _keyEventsOnly = false;
let _inspectedSeat = null;
let _notebookTrigger = null;
let _followLogs = true;
let _runtimeMode = 'live';
let _notes = { text: '', marks: {} };
const ROLE_TIPS = {
    villager: '听发言、看票型，用你的票找出狼人。',
    werewolf: '夜间与队友商议，白天藏好你的身份。',
    seer: '每夜查验一位玩家，找准时机传递信息。',
    witch: '解毒各一次；一晚一药，仅首夜可自救。',
    hunter: '死亡时可开枪；被毒无法发动技能。',
    idiot: '首度被放逐免死，之后无法投票。'
};
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const utcDate = value => new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + 'Z');

const ROLE_NAMES = {
    villager: "平民", werewolf: "狼人", seer: "预言家", witch: "女巫", hunter: "猎人", idiot: "白痴"
};
const PHASE_NAMES = {
    created: "等待开始", sheriff_campaign: "警长竞选", sheriff_withdraw: "退水环节", sheriff_vote: "警长投票",
    night_start: "夜晚", wolf_kill: "狼人行动", seer_check: "预言家查验",
    witch_act: "女巫行动", night_resolve: "天亮了", day_start: "白天", wolf_strategy: "狼队",
    day_speech: "白天讨论", day_vote: "投票环节", vote_resolve: "投票结算",
    hunter_check: "猎人技能", last_words: "遗言", win_check: "结算检查", game_end: "游戏结束"
};
const WINNER_NAMES = { good: "好人阵营获胜", evil: "狼人阵营获胜" };

// ── Auth helpers ──
function getToken() { return localStorage.getItem("ww_token"); }
function setToken(token) { localStorage.setItem("ww_token", token); }
function clearToken() { localStorage.removeItem("ww_token"); localStorage.removeItem("ww_nickname"); localStorage.removeItem("ww_username"); localStorage.removeItem("ww_is_admin"); }
function getNickname() { return localStorage.getItem("ww_nickname") || ""; }
function setNickname(n) { localStorage.setItem("ww_nickname", n); }
function getUsername() { return localStorage.getItem("ww_username") || ""; }
function setUsername(u) { localStorage.setItem("ww_username", u); }
function isAdminUser() { return localStorage.getItem("ww_is_admin") === "true"; }
function setAdminStatus(value) { localStorage.setItem("ww_is_admin", value === true ? "true" : "false"); }

function authHeaders() {
    const token = getToken();
    return token ? { "Authorization": "Bearer " + token, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
}

async function apiFetch(url, opts) {
    opts = opts || {};
    opts.headers = { ...authHeaders(), ...(opts.headers || {}) };
    const res = await fetch(url, opts);
    if (res.status === 401) {
        clearToken();
        show("login-screen");
        throw new Error("登录已失效，请重新登录");
    }
    if (!res.ok) {
        let message = '请求失败，请稍后重试';
        try { message = (await res.json()).detail || message; } catch (_) {}
        throw new Error(message);
    }
    return res;
}

// ── DOM helpers ──
const $ = (sel) => document.querySelector(sel);
const SCREEN_TITLES = {
    "login-screen": "AI 狼人杀",
    "home-screen": "AI 狼人杀 - 大厅",
    "stats-screen": "AI 狼人杀 - 统计",
    "model-settings-screen": "AI 狼人杀 - 模型配置",
    "game-screen": "AI 狼人杀 - 对局中",
    "gameover-screen": "AI 狼人杀 - 结算",
};
const show = (id) => {
    document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
    $(`#${id}`).classList.add("active");
    document.title = SCREEN_TITLES[id] || "AI 狼人杀";
    window.scrollTo(0, 0);
};

function getDisplayName(seat) {
    const p = players.find(p => p.seat === seat);
    const nick = p?.is_human ? getNickname() : p?.nickname || '';
    return nick ? `${seat}号(${escapeHTML(nick)})` : `${seat}号`;
}

function addLog(text, phase, logType) {
    if (String(text).startsWith('本局模型：') || String(text).startsWith('模拟阵容标签')) return;
    const container = $('#log-content');
    const following = _followLogs;
    const el = document.createElement('div');
    el.className = 'log-entry' + (logType ? ' ' + logType : ' system');
    el.dataset.round = _currentRound;
    el.dataset.key = (logType !== 'speech' && logType !== 'system') ? 'true' : 'false';
    if (logType === 'speech' || logType === 'explode') {
        const seatMatch = String(text).match(/(\d+)号/);
        const seat = seatMatch ? Number(seatMatch[1]) : null;
        const p = players.find(p => p.seat === seat);
        const content = String(text).replace(/<span class="log-seat">.*?<\/span>[：:]?\s*/, '');
        if (p?.is_human) el.classList.add('speech-self');
        el.innerHTML = `<div class="log-bubble-avatar">${portraitHTML(seat)}</div><div class="log-bubble"><div class="log-bubble-name">${escapeHTML(seat ? getDisplayName(seat) : '')}<span class="speech-tag">${escapeHTML(PHASE_NAMES[phase] || '发言')}</span></div><div class="log-bubble-content"></div></div>`;
        el.querySelector('.log-bubble-content').textContent = content;
        _speechRecords.push({seat, content, round: _currentRound, phase});
        if (_inspectedSeat === seat) inspectPlayer(seat);
    } else {
        el.textContent = String(text ?? '');
    }
    container.appendChild(el);
    updateRoundFilter();
    filterLogs();
    if (following) container.scrollTop = container.scrollHeight;
    else $('#new-messages').classList.remove('hidden');
}

function portraitHTML(seat) {
    // Seat portraits are cosmetic and never selected from the player's role.
    const index = Math.max(0, Math.min(11, Number(seat || 1) - 1));
    const x = (index % 4) * 100 / 3, y = Math.floor(index / 4) * 50;
    return `<span class="portrait" style="--portrait-x:${x}%;--portrait-y:${y}%" aria-hidden="true"></span>`;
}

function renderSeats() {
    const container = $('#seats-left');
    container.innerHTML = '';
    const sorted = [...players].sort((a,b) => a.seat - b.seat);
    // Three seats on each edge, numbered clockwise around a square table.
    const positions = [[2,1],[3,1],[4,1],[5,2],[5,3],[5,4],
                       [4,5],[3,5],[2,5],[1,4],[1,3],[1,2]];
    for (const p of sorted) {
        const div = document.createElement('button');
        div.className = 'seat'; div.id = `seat-${p.seat}`;
        const [column, row] = positions[p.seat - 1];
        div.style.setProperty('--seat-column', column);
        div.style.setProperty('--seat-row', row);
        div.style.setProperty('--avatar-hue', (p.seat * 37 + 140) % 360);
        if (!p.is_alive) div.classList.add('dead');
        if (p.is_human) div.classList.add('human');
        if (p.seat === sheriffSeat) div.classList.add('sheriff');
        if (p.seat === _currentSpeaker) div.classList.add('speaking');
        if (p.seat === _inspectedSeat) div.classList.add('selected');
        const isWolfTeammate = p.role === 'werewolf' && (godMode || (myRole === 'werewolf' && !p.is_human));
        if (isWolfTeammate) div.classList.add('wolf-teammate');
        const visibleRole = p.role ? ROLE_NAMES[p.role] : null;
        const name = p.is_human ? (getNickname() || '你') : (p.nickname || `玩家${p.seat}`);
        const identity = godMode ? (visibleRole || '身份未知')
            : p.is_human ? '你 · ' + (visibleRole || '存活')
            : isWolfTeammate ? '狼人队友' : visibleRole || '身份未知';
        const state = p.is_human && !p.is_alive ? '已出局'
            : identity + (p.is_alive && !p.can_vote ? ' · 无投票权' : '');
        const mark = _notes.marks[p.seat];
        const teamEmblem = isWolfTeammate ? '<span class="wolf-team-emblem" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 3l6 4h4l6-4-1 11-7 7-7-7z"/><path d="m7 11 2 1m8-1-2 1m-5 4 2 2 2-2"/></svg></span>' : '';
        div.innerHTML = `<div class="seat-avatar">${portraitHTML(p.seat)}${teamEmblem}<span class="seat-number">${p.seat}</span></div><div class="seat-info"><div class="seat-role">${escapeHTML(name)}</div><div class="seat-status${isWolfTeammate ? ' wolf-teammate-badge' : ''}">${escapeHTML(state)}</div></div>${mark ? `<span class="seat-mark">${escapeHTML(mark)}</span>` : ''}`;
        div.setAttribute('aria-label', `${p.seat}号 ${name} ${state}${!p.is_alive && !p.is_human ? ' 已出局' : ''}${p.seat===sheriffSeat?' 警长':''}，查看发言与标记`);
        div.onclick = () => { inspectPlayer(p.seat); toggleNotebook(true); };
        container.appendChild(div);
    }
    $('#alive-count').textContent = `${players.filter(p => p.is_alive).length} / ${players.length} 人存活`;
    syncIdentity();
}

function resetGameScreen() {
    $("#log-content").innerHTML = "";
    $("#phase-text").textContent = "等待开始";
    $("#round-text").textContent = "";
    $("#role-info").textContent = "";
    $("#game-timer").textContent = "00:00";
    $("#seats-left").innerHTML = "";
    hideAction();
    hideInputBar();
    stopGameTimer();
    sheriffSeat = null;
    _gameStartedAt = null; _currentRound = 0; _currentPhase = 'created';
    _currentSpeaker = null; _speechOrder = []; _speechRecords = [];
    _pendingPrompt = null; _sendingAction = false; _inspectedSeat = null;
    _keyEventsOnly = false; _notes = {text:'', marks:{}};
    $('#btn-key-events').classList.remove('active');
    $('#log-round-filter').innerHTML = '<option value="all">全部轮次</option>';
    $('#new-messages').classList.add('hidden');
    $('#private-note').value = ''; $('#player-inspector').innerHTML = '<div class="inspector-empty">点一下座位<br><span>记下身份倾向，或回看 TA 的发言。</span></div>';
    document.querySelector('.notebook-panel').classList.remove('open');
    _notebookTrigger = null;
    _followLogs = true;
    godMode = false;
    $('#notebook-backdrop').classList.add('hidden');
    $('#mobile-notes-button').setAttribute('aria-expanded','false');
    $('#mobile-notes-button').textContent='✎ 推理簿';
    setSpeaker(null); renderSpeechQueue(); stopSocket();
}

// ── Timer ──
function startGameTimer(startedAt) {
    _gameStartedAt = startedAt ? utcDate(startedAt) : new Date();
    stopGameTimer();
    _updateTimerDisplay();
    _gameTimerInterval = setInterval(_updateTimerDisplay, 1000);
}

function stopGameTimer() {
    if (_gameTimerInterval) {
        clearInterval(_gameTimerInterval);
        _gameTimerInterval = null;
    }
}

function _updateTimerDisplay() {
    if (!_gameStartedAt) return;
    const elapsed = Math.floor((Date.now() - _gameStartedAt.getTime()) / 1000);
    const h = Math.floor(elapsed / 3600);
    const m = Math.floor((elapsed % 3600) / 60);
    const s = elapsed % 60;
    const timerEl = $("#game-timer");
    if (timerEl) {
        timerEl.textContent = h > 0
            ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
            : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }
}

// ── Action panel (modal for choices) ──
function showAction(title, bodyHtml, buttons) {
    $("#action-title").textContent = title;
    $("#action-body").innerHTML = bodyHtml;
    const btnContainer = $("#action-buttons");
    btnContainer.innerHTML = "";
    for (const btn of buttons) {
        const b = document.createElement("button");
        if (btn._html) {
            b.innerHTML = btn._html;
        } else {
            b.textContent = btn.label;
        }
        b.className = btn.className || "";
        b.onclick = btn.onclick;
        btnContainer.appendChild(b);
    }
    $("#action-overlay").classList.add("active");
}

function hideAction() {
    $("#action-overlay").classList.remove("active");
}

function sendAction(action) {
    if (_sendingAction || !_pendingPrompt) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        $('#action-feedback').textContent = '连接已断开，草稿已保留。恢复连接后再提交。';
        return;
    }
    _sendingAction = true;
    setActionBusy(true);
    $('#action-feedback').textContent = '正在提交…';
    ws.send(JSON.stringify({...action, prompt_id: _pendingPrompt.prompt_id}));
}

function confirmTarget(action, label) {
    if (_sendingAction) return;
    _selectedAction = action;
    const preview = $('#selection-preview');
    preview.classList.remove('hidden');
    preview.innerHTML = `<span>${escapeHTML(label)}</span><button type="button">确认${['day_vote','sheriff_vote'].includes(action.action_type)?'投票':'操作'}</button>`;
    preview.querySelector('button').onclick = () => {
        if (_selectedAction.action_type === 'last_words') _selectedAction.content = $('#input-text').value;
        sendAction(_selectedAction);
    };
    document.querySelectorAll('#input-extra-buttons .seat-btn').forEach(b => b.classList.toggle('chosen', Number(b.dataset.seat) === action.target || Number(b.dataset.seat) === action.poison_target));
}

// ── Bottom input bar ──
let _inputBarActionType = null;


function showInputBar(label, actionType, opts) {
    opts = opts || {};
    _inputBarActionType = actionType;
    $("#input-label").textContent = label;
    $("#input-text").value = "";
    $('#input-row').style.display = '';
    $('#waiting-bar').classList.add('hidden');
    $('#speech-count').textContent = '0 / 500 · Ctrl / ⌘ + Enter 发送';
    $("#input-text").placeholder = opts.placeholder || "输入你的发言...";

    const extraContainer = $("#input-extra-buttons");
    extraContainer.innerHTML = "";
    if (opts.extraButtons) {
        for (const btn of opts.extraButtons) {
            const b = document.createElement("button");
            b.textContent = btn.label;
            b.onclick = btn.onclick;
            if (btn.className) b.className = btn.className;
            extraContainer.appendChild(b);
        }
    }

    const submitBtn = $("#input-submit");
    submitBtn.textContent = opts.submitLabel || "发送";
    submitBtn.onclick = () => {
        const text = $("#input-text").value;
        if (opts.onSubmit) {
            opts.onSubmit(text);
        } else {
            sendAction({ action_type: actionType, content: text });
        }
    };

    $("#input-bar").classList.add("active");
    setTimeout(() => $("#input-text").focus(), 50);
}

function hideInputBar() {
    $("#input-bar").classList.remove("active");
    $('#waiting-bar').classList.remove('hidden');
    $('#selection-preview').classList.add('hidden');
    $('#action-feedback').textContent = '';
    $("#input-row").style.display = "";
    _inputBarActionType = null;
}

function showChoiceBar(label, buttons) {
    $('#selection-preview').classList.add('hidden');
    _selectedAction = null;
    $('#waiting-bar').classList.add('hidden');
    $("#input-label").textContent = label;
    $("#input-row").style.display = "none";

    const extraContainer = $("#input-extra-buttons");
    extraContainer.innerHTML = "";
    for (const btn of buttons) {
        const b = document.createElement("button");
        if (btn._html) {
            b.innerHTML = btn._html;
        } else {
            b.textContent = btn.label;
        }
        b.className = btn.className || "";
        b.onclick = btn.onclick;
        if (btn.seat) b.dataset.seat = btn.seat;
        extraContainer.appendChild(b);
    }

    $("#input-bar").classList.add("active");
}

// ── Prompt handlers ──
function handlePrompt(promptType, data) {
    const samePrompt = _pendingPrompt?.prompt_id === data.prompt_id;
    const draft = samePrompt ? $('#input-text').value : '';
    _pendingPrompt = data; _sendingAction = false; _selectedAction = null;
    setActionBusy(false);
    $('#action-feedback').textContent = '';
    $('#selection-preview').classList.add('hidden');
    if (mySeat && ['day_speech','sheriff_speech','last_words'].includes(promptType)) setSpeaker(mySeat, false);
    startActionClock();
    switch (promptType) {
        case "sheriff_signup":
            showChoiceBar("警长竞选：是否上警？",
                [
                    { label: "上警", onclick: () => sendAction({ action_type: "sheriff_signup", run: true }) },
                    { label: "不上警", onclick: () => sendAction({ action_type: "sheriff_signup", run: false }) },
                ]);
            break;

        case "speech_direction":
            showChoiceBar(data.message || "你是警长，请选择发言方向（你将最后发言）",
                [
                    { label: "顺时针", onclick: () => sendAction({ action_type: "speech_direction", direction: "clockwise" }) },
                    { label: "逆时针", onclick: () => sendAction({ action_type: "speech_direction", direction: "counterclockwise" }) },
                ]);
            break;

        case "sheriff_speech":
            showInputBar("竞选演说", "sheriff_speech", {
                placeholder: "发表你的竞选演说...",
                submitLabel: "发表演说",
                extraButtons: data.is_wolf ? [
                    { label: "💣 自爆", className: "btn-explode", onclick: () => {
                        if (confirm("确定要自爆吗？自爆后将公开狼人身份并吞掉警徽，本局将无警长。")) {
                            sendAction({ action_type: "wolf_explode" });
                        }
                    }},
                ] : [],
            });
            break;

        case "sheriff_withdraw":
            showChoiceBar("退水确认：是否放弃竞选？",
                [
                    { label: "坚持竞选", onclick: () => sendAction({ action_type: "sheriff_withdraw", withdraw: false }) },
                    { label: "退水", onclick: () => sendAction({ action_type: "sheriff_withdraw", withdraw: true }) },
                ]);
            break;

        case "sheriff_vote":
            showChoiceBar("警长投票：选择你支持的候选人",
                [...buildTargetButtons(data.candidates.map(c => c.seat), (target) => {
                    confirmTarget({ action_type: 'sheriff_vote', target }, `支持 ${getDisplayName(target)} 当选警长`);
                }), {label:'弃票', onclick:() => sendAction({action_type:'sheriff_vote',target:null})}]);
            break;

        case "wolf_discuss": {
            if (data.wolf_discussion) {
                for (const d of data.wolf_discussion) {
                    addLog(`<span class="log-seat">${d.seat}号</span>（狼队友）：${d.speech || "建议击杀" + d.target + "号"}`, "wolf_kill", "speech");
                }
            }
            showInputBar("狼人讨论", "wolf_discuss", {
                placeholder: "和狼队友讨论...",
                submitLabel: "发送",
                extraButtons: [
                    { label: "跳过", onclick: () => sendAction({ action_type: "wolf_discuss", content: "" }) },
                ],
            });
            break;
        }

        case "wolf_kill": {
            showChoiceBar("狼人行动：选择击杀目标",
                buildTargetButtons(data.valid_targets, (target) => {
                    confirmTarget({ action_type: "wolf_kill", target }, `今晚击杀 ${getDisplayName(target)}`);
                }));
            break;
        }

        case "seer_check": {
            if (data.past_checks && data.past_checks.length) {
                addLog("已查验：" + data.past_checks.map(c => `${c.target}号=${c.result}`).join("，"), null, "system");
            }
            if (!data.valid_targets.length) {
                showChoiceBar('所有存活玩家均已查验', [{label:'结束查验',onclick:() => sendAction({action_type:'seer_check',target:null})}]);
                break;
            }
            showChoiceBar("预言家查验：选择要查验的玩家",
                buildTargetButtons(data.valid_targets, (target) => {
                    confirmTarget({ action_type: "seer_check", target }, `查验 ${getDisplayName(target)}`);
                }));
            break;
        }

        case "witch_act": {
            const witchInfo = `解药：${data.has_antidote ? "可用" : "已用"} | 毒药：${data.has_poison ? "可用" : "已用"}`;
            if (data.wolf_kill_target) {
                addLog(`今晚 ${data.wolf_kill_target}号 被狼人击杀`, "witch_act", "system");
            }
            const witchBtns = [];
            if ((data.can_save !== false) && data.has_antidote && data.wolf_kill_target) {
                witchBtns.push({ label: "使用解药", onclick: () => confirmTarget({action_type:'witch_act',save:true}, `使用解药救 ${getDisplayName(data.wolf_kill_target)}`) });
            }
            if (data.has_poison) {
                witchBtns.push({ label: "使用毒药...", onclick: () => {
                    showChoiceBar("选择毒药目标",
                        [...buildTargetButtons(data.valid_poison_targets, (target) => {
                            confirmTarget({ action_type:'witch_act', save:false, poison_target:target }, `使用毒药带走 ${getDisplayName(target)}`);
                        }), {label:'返回药水选择', onclick:() => handlePrompt('witch_act',data)}]);
                }});
            }
            witchBtns.push({ label: "不使用药水", onclick: () => sendAction({ action_type: "witch_act", save: false, poison_target: null }) });
            showChoiceBar(`女巫行动（${witchInfo}）`, witchBtns);
            break;
        }

        case "hunter_shot":
            showChoiceBar("猎人技能：选择开枪目标", [
                ...buildTargetButtons(data.valid_targets, (target) => {
                    confirmTarget({ action_type: "hunter_shot", target }, `开枪带走 ${getDisplayName(target)}`);
                }),
                { label: "不开枪", onclick: () => sendAction({ action_type: "hunter_shot", target: null }) },
            ]);
            break;

        case "day_speech":
            showInputBar("轮到你发言", "day_speech", {
                placeholder: "输入你的发言...",
                submitLabel: "发言",
                extraButtons: data.is_wolf ? [
                    { label: "💣 自爆", className: "btn-explode", onclick: () => {
                        if (confirm("确定要自爆吗？自爆后将公开狼人身份，跳过本轮投票直接进入夜晚。")) {
                            sendAction({ action_type: "wolf_explode" });
                        }
                    }},
                ] : [],
            });
            break;

        case "day_vote":
            showChoiceBar("投票：选择要淘汰的玩家",
                [...buildTargetButtons(data.votable_players.map(p => p.seat), (target) => {
                    confirmTarget({ action_type:'day_vote', target }, `放逐 ${getDisplayName(target)}`);
                }), {label:'弃票', onclick:() => sendAction({action_type:'day_vote',target:null})}]);
            break;

        case 'sheriff_transfer':
            showChoiceBar('移交警徽：本次没有遗言', [...buildTargetButtons((data.alive_players || []).map(p => p.seat), target => {
                confirmTarget({action_type:'sheriff_transfer',sheriff_transfer:target}, `移交警徽给 ${getDisplayName(target)}`);
            }), {label:'撕掉警徽',onclick:() => confirmTarget({action_type:'sheriff_transfer',sheriff_transfer:0}, '撕掉警徽，之后不再有警长')}]);
            break;
        case "last_words": {
            if (data.is_sheriff) {
                const alivePlayers = data.alive_players || [];
                showInputBar("遗言 + 警徽移交", "last_words", {
                    placeholder: "输入你的遗言...",
                    submitLabel: "发言",
                    extraButtons: [...alivePlayers.map(p => ({
                        label: `移交${p.seat}号`,
                        onclick: () => {
                            const text = $("#input-text").value;
                            confirmTarget({action_type:'last_words',content:text,sheriff_transfer:p.seat}, `移交警徽给 ${getDisplayName(p.seat)} 并发表遗言`);
                        },
                    })), {label:'撕掉警徽',onclick:() => confirmTarget({action_type:'last_words',content:$('#input-text').value,sheriff_transfer:0}, '撕掉警徽并发表遗言')}],
                    onSubmit: (text) => {
                        confirmTarget({action_type:'last_words',content:text,sheriff_transfer:0}, '未指定接任者，将撕掉警徽并发表遗言');
                    },
                });
            } else {
                showInputBar("遗言", "last_words", {
                    placeholder: "输入你的遗言...",
                    submitLabel: "发言",
                });
            }
            break;
        }
    }
    if (samePrompt && draft) { $('#input-text').value = draft; updateSpeechCount(); }
}

function buildTargetButtons(seats, callback) {
    return seats.map(seat => {
        return {
            label: `${seat}号`, seat,
            className: "seat-btn",
            onclick: () => callback(seat),
            _html: `<span class="seat-btn-avatar" title="${escapeHTML(getDisplayName(seat))}">${portraitHTML(seat)}<b>${seat}</b></span><span class="target-label">${seat}号</span>`,
        };
    });
}

// ── WebSocket ──
function stopSocket() {
    _wsGeneration++;
    clearTimeout(_reconnectTimer); _reconnectTimer = null;
    if (ws) { const old = ws; ws = null; old.close(); }
    clearInterval(_actionClock); _actionClock = null;
}

function connectWS(gId) {
    stopSocket();
    const generation = _wsGeneration;
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/ws/games/${gId}?token=${encodeURIComponent(getToken() || '')}`;
    function connect() {
        if (generation !== _wsGeneration || gameId !== gId) return;
        const socket = new WebSocket(url); ws = socket;
        socket.onopen = () => { if (ws === socket) setConnection(true); };
        socket.onmessage = event => {
            if (ws !== socket) return;
            try { handleWSMessage(JSON.parse(event.data)); } catch (e) { console.error('消息处理失败', e); }
        };
        socket.onclose = e => {
            if (generation !== _wsGeneration || ws !== socket) return;
            setConnection(false); _sendingAction = false;
            setActionBusy(false);
            if (e.code === 4001) { clearToken(); stopSocket(); show('login-screen'); return; }
            if (e.code === 1000) { stopSocket(); $('#connection-status').textContent = '已在另一窗口连接'; return; }
            $('#action-feedback').textContent = '连接中断，正在重连；草稿已保留。';
            _reconnectTimer = setTimeout(connect, 2000);
        };
        socket.onerror = () => setConnection(false);
    }
    connect();
}

function handleWSMessage(msg) {
    const { type, data } = msg;

    switch (type) {
        case "connected":
            if (_pendingPrompt && _pendingPrompt.prompt_id !== data.pending_prompt_id) {
                _pendingPrompt = null; _sendingAction = false;
                hideInputBar(); clearInterval(_actionClock);
            }
            if (data.state) {
                godMode = data.state.mode === 'god' || data.god_mode === true;
                players = data.state.players; sheriffSeat = data.state.sheriff_player_seat;
                _currentRound = data.state.current_round;
                setPhase(data.state.current_phase, _currentRound);
                setSpeaker(null);
                renderSeats();
                if (data.state.status === 'finished') { enterGame(gameId,'finished'); break; }
            }
            if (data.events) renderHistory(data.events);
            setConnection(true);
            if (data.interrupted) {
                showGameInterruption('正在检查可恢复的位置；若当前步骤尚未完成，可结束本局再开始新对局。');
            }
            if (data.game_started_at) {
                startGameTimer(data.game_started_at);
            }
            break;

        case 'phase_change':
            setPhase(data.phase, data.round);
            hideAction(); hideInputBar();
            _currentSpeaker = null; _pendingPrompt = null; _sendingAction = false;
            clearInterval(_actionClock);
            setSpeaker(null); renderSeats();
            if (!_gameTimerInterval && !_gameStartedAt) startGameTimer(null);
            break;
        case 'game_resumed':
            _pendingPrompt = null; _sendingAction = false;
            hideAction(); hideInputBar();
            syncIdentity(); setSpeaker(null);
            addLog(data.message || '对局已恢复');
            break;
        case 'speech_order':
            _speechOrder = data.seats || []; renderSpeechQueue();
            break;
        case 'speaker_change':
            setSpeaker(data.seat, !data.is_human);
            break;
        case 'speech':
            if (data.seat) {
                const announcement = /^\d+号玩家(选择上警|选择不上警|选择退水|坚持竞选)/.test(data.content);
                addLog(announcement ? data.content : `<span class="log-seat">${data.seat}号</span>：${data.content}`, data.phase, announcement?'system':data.phase==='wolf_explode'?'explode':'speech');
                if (!announcement && !['wolf_discuss','wolf_explode'].includes(data.phase)) setSpeaker(data.seat, false);
                if (data.phase==='wolf_explode') { const p=players.find(p=>p.seat===data.seat); if(p) p.role='werewolf'; }
            }
            break;
        case 'action_accepted':
            if (data.prompt_id === _pendingPrompt?.prompt_id) {
                _pendingPrompt = null; _sendingAction = false; clearInterval(_actionClock);
                hideAction(); hideInputBar();
                $('#waiting-title').textContent = '你的操作已提交';
                $('#waiting-tip').textContent = '等待其他玩家完成行动。';
            }
            break;

        case "idiot_reveal":
            addLog(data.message, null, "highlight-vote");
            if (data.seat) {
                const revealed = players.find(p => p.seat === data.seat);
                if (revealed) {
                    revealed.role = "idiot";
                    revealed.can_vote = false;
                    revealed.is_alive = true;
                }
                renderSeats();
            }
            break;

        case "death":
            if (data.deaths) {
                for (const d of data.deaths) {
                    const p = players.find(p => p.seat === d.seat);
                    if (p) p.is_alive = false;
                }
                renderSeats();
            }
            if (data.message) {
                let deathLogType = "highlight-kill";
                if (data.deaths && data.deaths.length === 1) {
                    const cause = data.deaths[0].cause;
                    if (cause === "witch_poison") deathLogType = "highlight-poison";
                    else if (cause === "hunter_shot") deathLogType = "highlight-hunter";
                }
                addLog(data.message, null, deathLogType);
            }
            break;

        case "vote_result": {
            addVoteCard(data, '放逐投票');
            if (data.idiot_revealed) {
                const revealed = players.find(p => p.seat === data.idiot_revealed);
                if (revealed) {
                    revealed.role = "idiot";
                    revealed.can_vote = false;
                    revealed.is_alive = true;
                }
                renderSeats();
            } else if (data.voted_out) {
                const p = players.find(p => p.seat === data.voted_out);
                if (p) p.is_alive = false;
                renderSeats();
            }
            break;
        }

        case 'sheriff_vote_result':
            addVoteCard(data, '警长投票');
            if (data.elected) { sheriffSeat = data.elected; renderSeats(); }
            break;

        case "info":
            addLog(data.message, null, data.highlight ? `highlight-${data.highlight}` : null);
            if (data.message) {
                const transfer = data.message.match(/警徽移交给(\d+)号/);
                if (transfer) { sheriffSeat = Number(transfer[1]); renderSeats(); }
                if (/撕掉了警徽|警徽被销毁|警徽被吞/.test(data.message)) { sheriffSeat = null; renderSeats(); }
                const match = data.message.match(/(\d+)号玩家当选警长/);
                if (match) {
                    sheriffSeat = parseInt(match[1]);
                    renderSeats();
                }
            }
            break;

        case 'god_event':
            if (data.event_type === 'wolf_discuss' && data.actor_seat) {
                addLog(`<span class="log-seat">${data.actor_seat}号</span>：${data.content || '……'}`, 'wolf_strategy', 'speech');
            } else {
                const line = privateEventLine(data);
                if (line) addLog(line.text, data.phase, line.kind);
            }
            break;

        case "prompt_action":
            if (!godMode) handlePrompt(data.prompt_type, data);
            break;

        case "game_end":
            stopGameTimer();
            showGameOver(data);
            break;

        case 'error':
            _sendingAction = false;
            setActionBusy(false);
            $('#action-feedback').textContent = data.message;
            addLog('提示：' + data.message, null, 'system');
            if ((data.message || '').startsWith('对局流程已中断')) {
                showGameInterruption(data.message);
            }
            break;
    }
}

// ── Game over ──
function showGameOver(data) {
    $("#gameover-title").textContent = data.winner_text;
    stopSocket(); hideInputBar();
    const iconEl = $("#gameover-icon");
    if (iconEl) {
        iconEl.classList.toggle('role-art', Boolean(data.winner));
        iconEl.dataset.role = data.winner === 'evil' ? 'werewolf' : 'villager';
        iconEl.textContent = data.winner ? '' : '☾';
    }
    const container = $("#gameover-roles");
    container.innerHTML = "";
    for (const p of data.players) {
        const div = document.createElement("div");
        const isWolf = p.role === "werewolf";
        div.className = `role-card ${isWolf ? "wolf" : "good"}`;
        if (p.is_human) div.classList.add("is-me");
        const nick = p.nickname || (p.is_human ? (localStorage.getItem('ww_nickname') || '你') : '');
        const modelText = p.model_name
            ? `<div class="rc-model">${escapeHTML(p.model_name)}</div>`
            : "";
        const youTag = p.is_human ? `<span class="rc-you-tag">你</span>` : "";
        div.innerHTML = `
            <div class="rc-avatar role-art" data-role="${escapeHTML(p.role)}" aria-hidden="true"></div>
            <div class="rc-info">
                <div class="rc-top">
                    <span class="rc-seat">${p.seat}号</span>
                    <span class="rc-role">${ROLE_NAMES[p.role] || p.role}</span>
                    ${youTag}
                </div>
                ${nick ? `<div class="rc-nick">${escapeHTML(nick)}</div>` : ""}
                ${modelText}
            </div>
            <div class="rc-right">
                <div class="rc-status ${p.is_alive ? "alive" : "dead"}">${p.is_alive ? "存活" : "阵亡"}</div>
            </div>
        `;
        container.appendChild(div);
    }
    const eventsContainer = $("#gameover-events");
    eventsContainer.innerHTML = "";
    if (data.key_events && data.key_events.length) {
        const title = document.createElement("h3");
        title.className = "text-white text-base font-bold mb-3 flex items-center gap-2";
        title.textContent = '对局记录';
        eventsContainer.appendChild(title);
        const timeline = document.createElement("div");
        timeline.className = "timeline";
        for (const evt of data.key_events) {
            const item = document.createElement("div");
            item.className = "timeline-item";
            item.innerHTML = `<div class="timeline-dot dot-${evt.type}"></div>${escapeHTML(evt.text)}`;
            timeline.appendChild(item);
        }
        eventsContainer.appendChild(timeline);
    }
    show("gameover-screen");
}

// ── Login / Logout ──
async function login() {
    const username = $("#login-username").value.trim();
    const password = $("#login-password").value;
    if (!username || !password) {
        $("#login-status").textContent = "请输入用户名和密码";
        return;
    }
    $("#btn-login").disabled = true;
    $("#login-status").textContent = "登录中...";

    try {
        const res = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "登录失败");
        }
        const data = await res.json();
        setToken(data.token);
        setNickname(data.nickname);
        setUsername(data.username);
        setAdminStatus(data.is_admin);
        $("#login-status").textContent = "";
        await onLoginSuccess();
    } catch (e) {
        $("#login-status").textContent = e.message;
    } finally {
        $("#btn-login").disabled = false;
    }
}

async function logout() {
    try {
        await apiFetch("/api/auth/logout", { method: "POST" });
    } catch (e) { /* ignore */ }
    clearToken();
    gameId = null;
    stopSocket();
    stopGameTimer();
    history.pushState(null, "", "/");
    show("login-screen");
}

function goHome() {
    gameId = null;
    stopSocket();
    stopGameTimer();
    history.pushState(null, "", "/");
    showHomeScreen();
}

async function confirmEndGame() {
    if (!gameId) return;
    if (!confirm("确定要结束本局游戏吗？游戏将直接终止，无法恢复。")) return;
    try {
        await apiFetch(`/api/games/${gameId}/end`, { method: "POST" });
    } catch (e) {
        alert("结束游戏失败：" + (e.message || e));
    }
}

// ── Home screen ──
async function onLoginSuccess() {
    // Check URL for game restore
    const match = location.pathname.match(/^\/game\/(\d+)/);
    if (match) {
        await restoreGame(parseInt(match[1]));
    } else {
        await showHomeScreen();
    }
}

async function showHomeScreen() {
    $("#user-nickname").textContent = getNickname();
    const adminBtn = $("#btn-admin");
    $('#btn-model-settings').classList.toggle('hidden', !isAdminUser());
    if (isAdminUser()) {
        adminBtn.classList.remove("hidden");
    } else {
        adminBtn.classList.add("hidden");
    }
    show("home-screen");
    await Promise.all([loadGameList(), refreshGameAvailability()]);
}

window.addEventListener('focus', () => {
    if (getToken() && $('#home-screen').classList.contains('active') && !_creatingGame) {
        refreshGameAvailability();
    }
});

let _gameListPage = 1;
const _gameListPageSize = 10;

async function loadGameList(page) {
    if (page !== undefined) _gameListPage = page;
    const listEl = $("#game-list");
    const emptyEl = $("#game-list-empty");
    listEl.innerHTML = "<div class='loading'>加载中...</div>";
    emptyEl.classList.add("hidden");
    // Remove existing pagination
    const oldPager = document.querySelector(".pager");
    if (oldPager) oldPager.remove();

    try {
        const res = await apiFetch(`/api/games?page=${_gameListPage}&page_size=${_gameListPageSize}`);
        const data = await res.json();
        const games = data.items;
        const total = data.total;
        const totalPages = Math.ceil(total / _gameListPageSize);

        listEl.innerHTML = "";
        if (!games.length) {
            emptyEl.classList.remove("hidden");
            return;
        }

        for (const g of games) {
            const div = document.createElement("a");
            div.className = "game-list-item";
            div.href = `/game/${g.id}`;

            const isWolf = g.my_role === "werewolf";
            const statusText = g.status === "finished"
                ? (WINNER_NAMES[g.winner] || "已结束")
                : g.status === "running" ? "进行中" : "等待中";
            const statusClass = g.status === "finished" ? "finished" : g.status === "running" ? "running" : "waiting";
            const roleText = g.mode === 'god' ? '上帝视角' : (g.my_role ? (ROLE_NAMES[g.my_role] || g.my_role) : "");
            const dateText = utcDate(g.created_at).toLocaleString('zh-CN');
            const iconClass = g.mode === 'god' ? 'neutral-icon' : (isWolf ? "wolf-icon" : (g.my_role ? "good-icon" : "neutral-icon"));

            div.innerHTML = `
                <div class="game-list-left">
                    <div class="game-list-icon role-art ${iconClass}" data-role="${escapeHTML(g.my_role || '')}" aria-hidden="true"></div>
                    <div class="game-list-meta">
                        <div class="game-list-top">
                            <span class="game-list-id">#${g.id}</span>
                            ${roleText ? `<span class="game-list-role">${g.mode === 'god' ? roleText : `${g.my_seat}号·${roleText}`}</span>` : ""}
                        </div>
                        <div class="game-list-bottom">
                            <span class="game-list-round">第${g.current_round}轮</span>
                            <span class="game-list-date">${dateText}</span>
                        </div>
                    </div>
                </div>
                <div class="game-list-right">
                    <span class="game-list-status ${statusClass}">${statusText}</span>
                </div>
            `;
            div.onclick = event => {
                if(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                enterGame(g.id, g.status);
            };
            listEl.appendChild(div);
        }

        // Render pagination if more than 1 page
        if (totalPages > 1) {
            const pager = document.createElement("div");
            pager.className = "pager";
            const prevDisabled = _gameListPage <= 1;
            const nextDisabled = _gameListPage >= totalPages;
            pager.innerHTML = `
                <button class="pager-btn" ${prevDisabled ? "disabled" : ""} onclick="loadGameList(${_gameListPage - 1})">上一页</button>
                <span class="pager-info">${_gameListPage} / ${totalPages}</span>
                <button class="pager-btn" ${nextDisabled ? "disabled" : ""} onclick="loadGameList(${_gameListPage + 1})">下一页</button>
            `;
            listEl.parentNode.insertBefore(pager, listEl.nextSibling);
        }
    } catch (e) {
        listEl.innerHTML = `<div class="error">加载失败：${escapeHTML(e.message)}</div>`;
    }
}

async function enterGame(gId, status) {
    if (status === "finished") {
        // Show review
        try {
            const res = await apiFetch(`/api/games/${gId}/review`);
            const review = await res.json();
            history.pushState(null, "", `/game/${gId}`);
            showGameOver({
                winner: review.winner,
                winner_text: WINNER_NAMES[review.winner] || "游戏结束",
                players: review.players.map(p => ({
                    ...p,
                    role_name: ROLE_NAMES[p.role] || p.role,
                })),
                key_events: review.key_events,
            });
        } catch (e) {
            alert("加载失败：" + e.message);
        }
    } else {
        await restoreGame(gId);
    }
}

// ── Create game ──
async function createGame() {
    const btn = $("#btn-create");
    if (!_canCreateGame || _creatingGame || btn.disabled) return;
    _creatingGame = true;
    btn.disabled = true;
    btn.innerHTML = "创建中...";
    resetGameScreen();

    try {
        const res = await apiFetch("/api/games", {
            method: "POST",
            body: JSON.stringify({ mode: _gameMode }),
        });
        const game = await res.json();
        gameId = game.id;
        players = game.players;
        godMode = game.mode === 'god';
        mySeat = godMode ? null : players.find(p => p.is_human)?.seat;
        myRole = godMode ? null : players.find(p => p.is_human)?.role;

        show("game-screen");
        history.pushState(null, "", `/game/${gameId}`);
        loadNotes(); renderSeats();
        $('#room-label').textContent = `房间 #${gameId} · 预女猎白`;
        addLog(godMode ? '上帝模式已开始。十二个座位都是 AI，身份和夜间行动对你可见。' : `游戏已创建，你是${mySeat}号位 ${ROLE_NAMES[myRole] || myRole}`);

        connectWS(gameId);
        await apiFetch(`/api/games/${gameId}/start`, { method:'POST' });

    } catch (e) {
        alert("创建失败：" + e.message);
        if (getToken()) await refreshGameAvailability();
    } finally {
        _creatingGame = false;
        btn.disabled = !_canCreateGame;
        btn.innerHTML = startButtonLabel();
    }
}

// ── Restore game ──
async function restoreGame(gId) {
    resetGameScreen();
    try {
        const res = await apiFetch(`/api/games/${gId}`);
        const game = await res.json();

        gameId = game.id;
        players = game.players;
        godMode = game.mode === 'god';
        mySeat = godMode ? null : players.find(p => p.is_human)?.seat;
        myRole = godMode ? null : players.find(p => p.is_human)?.role;
        sheriffSeat = game.sheriff_player_seat;

        if (game.status === "finished") {
            // Show review instead
            const reviewRes = await apiFetch(`/api/games/${gId}/review`);
            const review = await reviewRes.json();
            showGameOver({
                winner: review.winner,
                winner_text: WINNER_NAMES[review.winner] || "游戏结束",
                players: review.players,
                key_events: review.key_events,
            });
            return;
        }

        show("game-screen");
        history.pushState(null, "", `/game/${gameId}`);
        setPhase(game.current_phase, game.current_round);
        $('#room-label').textContent = `房间 #${gameId} · 预女猎白`;
        loadNotes(); renderSeats();
        // The WebSocket snapshot restores the visible transcript and pending action together.

        // Start timer
        if (game.started_at) {
            startGameTimer(game.started_at);
        }

        // Connect WebSocket
        connectWS(gameId);
        if (game.status === 'waiting') await apiFetch(`/api/games/${gameId}/start`, {method:'POST'});

    } catch (e) {
        alert("恢复游戏失败：" + e.message);
        await showHomeScreen();
    }
}

// ── Stats ──
let currentStatsTab = "by-user";

function showStats() {
    show("stats-screen");
    switchStatsTab("by-user");
}

async function switchStatsTab(tab) {
    currentStatsTab = tab;
    $("#tab-by-user").classList.toggle("active", tab === "by-user");
    $("#tab-by-model").classList.toggle("active", tab === "by-model");
    $("#tab-by-human").classList.toggle("active", tab === "by-human");
    const container = $("#stats-table");
    container.innerHTML = "<div class='loading'>加载中...</div>";

    try {
        const res = await apiFetch(`/api/stats/${tab}`);
        const data = await res.json();
        if (!data.length) {
            container.innerHTML = "<div class='stats-empty'>暂无数据，完成几局游戏后再来看看</div>";
            return;
        }

        function rankClass(i) {
            if (i === 0) return "top-1";
            if (i === 1) return "top-2";
            if (i === 2) return "top-3";
            return "";
        }

        if (tab === "by-user") {
            container.innerHTML = `<div class="stats-list">
                ${data.map((r, i) => `<div class="stats-row">
                    <span class="stats-rank ${rankClass(i)}">${i + 1}</span>
                    <span class="stats-name">${escapeHTML(r.nickname)}</span>
                    <span class="stats-detail">${r.total}局</span>
                    <span class="stats-value">${r.win_rate}%</span>
                    <span class="stats-detail">狼${r.wolf_win_rate}% 好${r.good_win_rate}%</span>
                </div>`).join("")}
            </div>`;
        } else if (tab === "by-model") {
            container.innerHTML = `<div class="stats-list">
                ${data.map((r, i) => `<div class="stats-row">
                    <span class="stats-rank ${rankClass(i)}">${i + 1}</span>
                    <span class="stats-name stats-model-name">${escapeHTML(r.model_name || "-")}</span>
                    <span class="stats-detail">${r.total}局</span>
                    <span class="stats-value">${r.win_rate}%</span>
                    <span class="stats-detail">狼${r.wolf_win_rate}% 好${r.good_win_rate}%</span>
                </div>`).join("")}
            </div>`;
        } else if (tab === "by-human") {
            container.innerHTML = `<div class="stats-list">
                ${data.map((r, i) => `<div class="stats-row">
                    <span class="stats-rank ${rankClass(i)}">${i + 1}</span>
                    <span class="stats-name">${escapeHTML(r.nickname)}</span>
                    <span class="stats-value">${r.win_rate}%</span>
                    <span class="stats-detail">狼${r.wolf_win_rate}% 好${r.good_win_rate}%</span>
                </div>`).join("")}
            </div>`;
        }
    } catch (e) {
        container.innerHTML = `<div class='error'>加载失败：${escapeHTML(e.message)}</div>`;
    }
}

// ── Create user (admin) ──
function showCreateUser() {
    $("#new-username").value = "";
    $("#create-user-result").classList.add("hidden");
    $("#create-user-result").textContent = "";
    $("#create-user-overlay").classList.remove("hidden");
}

function hideCreateUser() {
    $("#create-user-overlay").classList.add("hidden");
}

async function doCreateUser() {
    const username = $("#new-username").value.trim();
    if (!username) { alert("请输入用户名"); return; }
    try {
        const res = await apiFetch("/api/admin/create-user", {
            method: "POST",
            body: JSON.stringify({ username }),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "创建失败");
        }
        const data = await res.json();
        const resultEl = $("#create-user-result");
        resultEl.innerHTML = `创建成功！<br>用户名：<strong>${escapeHTML(data.username)}</strong><br>密码：<strong>${escapeHTML(data.password)}</strong><br><small>请妥善保存密码，无法再次查看</small>`;
        resultEl.classList.remove("hidden");
    } catch (e) {
        alert(e.message);
    }
}

function showGameInterruption(message) {
    _pendingPrompt = null; _sendingAction = false;
    clearInterval(_actionClock); _actionClock = null;
    hideAction(); hideInputBar(); setSpeaker(null);
    $('#waiting-title').textContent = '对局进程已中断';
    $('#waiting-tip').textContent = message;
    $('#speaker-title').textContent = '对局进程已中断';
    $('#speaker-subtitle').textContent = message;
}

function setConnection(online) {
    const el = $('#connection-status');
    el.textContent = online ? '已连接' : '重连中';
    el.classList.toggle('offline', !online);
}
function setActionBusy(busy) {
    document.querySelectorAll('#input-bar button').forEach(button => button.disabled = busy);
}
function selectGameMode(mode) {
    _gameMode = mode === 'god' ? 'god' : 'seat';
    document.querySelectorAll('[data-game-mode]').forEach(button => {
        const selected = button.dataset.gameMode === _gameMode;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
    const note = $('#hero-mode-note');
    if (note) note.textContent = _gameMode === 'god' ? '12 AI · 你旁观' : '11 AI + 你';
    const caption = $('#cast-caption');
    if (caption) caption.textContent = _gameMode === 'god' ? '十二位对手，你在月下看完全程。' : '十一位对手，各有心思。';
    const button = $('#btn-create');
    if (button && !_creatingGame) button.innerHTML = startButtonLabel();
    if (getToken()) refreshGameAvailability();
}

function startButtonLabel() {
    return _gameMode === 'god' ? '上帝视角 · 观看对局 <span>→</span>' : '进入村庄 · 开始对局 <span>→</span>';
}

function privateEventLine(event) {
    const who = event.actor_seat ? `${event.actor_seat}号` : '';
    if (event.event_type === 'wolf_kill') return {text: event.content || `狼人决定击杀${event.target_seat}号`, kind: 'highlight-kill'};
    if (event.event_type === 'seer_result') return {text: `第${event.round}夜 ${who}查验 ${event.target_seat}号：${event.content}`, kind: 'highlight-seer'};
    if (event.event_type === 'witch_save') return {text: `第${event.round}夜 ${who}使用解药，目标 ${event.target_seat}号`, kind: 'highlight-save'};
    if (event.event_type === 'witch_poison') return {text: event.content || `第${event.round}夜 ${who}毒了${event.target_seat}号`, kind: 'highlight-poison'};
    if (event.content) return {text: `${who}内心：${event.content}`, kind: 'god'};
    return null;
}

function syncIdentity() {
    const eyebrow = document.querySelector('.identity-card .eyebrow');
    if (godMode) {
        if (eyebrow) eyebrow.textContent = '旁观 · 上帝视角';
        $('#role-info').textContent = '上帝视角';
        $('#identity-icon').textContent = '☾';
        $('#identity-icon').classList.remove('role-art');
        $('#identity-icon').dataset.role = '';
        $('#role-tip').textContent = '你不入座。十二位 AI 的身份、夜间行动和内心都可见。';
        $('#waiting-title').textContent = '正在旁观';
        $('#waiting-tip').textContent = '这一局没有需要你提交的操作。';
        return;
    }
    if (eyebrow) eyebrow.textContent = '你的身份 · 仅你可见';
    $('#role-info').textContent = mySeat ? `${mySeat}号 · ${ROLE_NAMES[myRole] || '身份待揭晓'}` : '身份待揭晓';
    $('#identity-icon').textContent = '';
    $('#identity-icon').dataset.role = myRole || '';
    $('#identity-icon').classList.add('role-art');
    $('#role-tip').textContent = ROLE_TIPS[myRole] || '你的身份仅自己可见。';
    const human = players.find(p=>p.is_human);
    $('#waiting-title').textContent = human && !human.is_alive ? '你已出局，正在旁观' : '等待其他玩家行动';
    $('#waiting-tip').textContent = human && !human.is_alive ? '游戏结束时会揭晓所有人的身份。' : '轮到你时，行动区会自动亮起。';
}
function setPhase(phase, round) {
    _currentPhase = phase;
    $('#game-screen').dataset.time = ['night_start','wolf_kill','seer_check','witch_act'].includes(phase) ? 'night' : 'day';
    if (round !== undefined) _currentRound = round;
    const night = ['night_start','wolf_kill','seer_check','witch_act'].includes(phase);
    $('#phase-symbol').textContent = night ? '☾' : '☀';
    $('#phase-text').textContent = PHASE_NAMES[phase] || phase;
    $('#round-text').textContent = _currentRound ? `第 ${_currentRound} ${night?'夜':'天'}` : '准备入座';
    $('#table-phase').textContent = PHASE_NAMES[phase] || '静待夜幕降临';
    $('#table-hint').textContent = godMode
        ? (night ? '夜间行动、查验和狼队讨论都对你可见' : '你能看见全部身份')
        : (night ? '夜间行动仅当事人可见' : '听完发言，再作判断');
    if (!['day_speech','sheriff_campaign'].includes(phase)) { _speechOrder=[]; renderSpeechQueue(); }
    updateRoundFilter();
}
function setSpeaker(seat, thinking = false) {
    _currentSpeaker = seat;
    document.querySelector('#speaker-focus .speaker-orb').innerHTML = seat ? portraitHTML(seat) : '<span class="focus-moon">☾</span>';
    $('#speaker-focus').classList.toggle('thinking', thinking);
    $('#speaker-title').textContent = seat ? `${getDisplayName(seat)}${thinking?' 正在斟酌发言':''}` : (PHASE_NAMES[_currentPhase] || '等待对局开始');
    const idle = godMode ? '十二位 AI 会自行发言、用技能和投票。' : '你的判断只来自已公开的信息与自己的技能。';
    $('#speaker-subtitle').textContent = seat ? (seat===mySeat ? '轮到你了，说出你的判断。' : thinking ? '留意 TA 会回应谁，又会把票带向谁。' : '发言已送达，可在下方回看原话。') : idle;
    document.querySelectorAll('.seat').forEach(el=>el.classList.toggle('speaking',el.id===`seat-${seat}`));
    renderSpeechQueue();
}
function renderSpeechQueue() {
    $('#speech-queue').innerHTML = _speechOrder.length ? '<span class="queue-label">发言顺序</span>' + _speechOrder.map(seat=>`<span class="queue-seat ${seat===_currentSpeaker?'current':''}">${seat}</span>`).join('') : '';
}
function updateRoundFilter() {
    const select = $('#log-round-filter');
    if (_currentRound && ![...select.options].some(o=>o.value===String(_currentRound))) {
        const o=document.createElement('option'); o.value=_currentRound; o.textContent=`第 ${_currentRound} 轮`; select.appendChild(o);
    }
}
function filterLogs() {
    const round = $('#log-round-filter').value;
    $('#log-content').querySelectorAll('.log-entry').forEach(el=>el.classList.toggle('filtered-out', (round!=='all' && el.dataset.round!==round) || (_keyEventsOnly && el.dataset.key!=='true')));
}
function toggleKeyEvents() { _keyEventsOnly=!_keyEventsOnly; $('#btn-key-events').classList.toggle('active',_keyEventsOnly); filterLogs(); }
function jumpToLatest() { _followLogs=true; $('#log-content').scrollTop=$('#log-content').scrollHeight; $('#new-messages').classList.add('hidden'); }
function addVoteCard(data, title) {
    const container=$('#log-content');
    const following=_followLogs;
    const el=document.createElement('div');el.className='log-entry vote-card';el.dataset.round=_currentRound;el.dataset.key='true';
    const tally=Object.entries(data.tally||{}).sort((a,b)=>b[1]-a[1]);
    const max=Math.max(1,...tally.map(([,count])=>count));
    const outcome=data.summary || (data.elected?`${data.elected}号当选`:data.idiot_revealed?`${data.idiot_revealed}号翻牌免死`:data.voted_out?`${data.voted_out}号出局`:tally.length&&data.is_tie?'平票':tally.length?'投票结束':'全部弃票');
    el.innerHTML=`<div class="vote-card-title">${escapeHTML(title)} · ${escapeHTML(outcome)}</div><div class="vote-grid">${Object.entries(data.votes||{}).map(([v,t])=>`<span class="vote-chip">${escapeHTML(v)} → ${t===null?'弃票':escapeHTML(t)}</span>`).join('')}</div>${tally.map(([seat,count])=>`<div class="vote-bar"><span>${escapeHTML(seat)}号</span><span class="vote-bar-track"><i style="width:${Number(count)/max*100}%"></i></span><span>${Number(count)}票</span></div>`).join('')}`;
    container.appendChild(el);updateRoundFilter();filterLogs();
    if(following) container.scrollTop=container.scrollHeight; else $('#new-messages').classList.remove('hidden');
}
function renderHistory(events) {
    const savedRound=_currentRound;
    const container=$('#log-content'); const oldTop=container.scrollTop;
    const following=container.scrollHeight-container.scrollTop-container.clientHeight<85;
    container.innerHTML=''; _speechRecords=[];
    let historySheriff=null;
    for(const e of events) {
        _currentRound=e.round;
        if(['sheriff_elected','sheriff_transfer'].includes(e.event_type)) historySheriff=e.target_seat || null;
        if(e.event_type==='wolf_explode' && e.phase?.startsWith('sheriff')) historySheriff=null;
        if (godMode && e.visibility && e.visibility !== 'all') {
            if (e.event_type === 'wolf_discuss' && e.actor_seat) {
                addLog(`<span class="log-seat">${e.actor_seat}号</span>：${e.content || '……'}`, 'wolf_strategy', 'speech');
            } else {
                const line = privateEventLine(e);
                if (line) addLog(line.text, e.phase, line.kind);
            }
            continue;
        }
        if(['day_speech','sheriff_campaign','last_words','wolf_discuss'].includes(e.event_type) && e.actor_seat) {
            addLog(`<span class="log-seat">${e.actor_seat}号</span>：${e.content||'过。'}`,e.event_type,'speech');
        } else if(['vote_result','sheriff_vote_result'].includes(e.event_type)) {
            const votes={},tally={}; const sheriffVote=e.event_type==='sheriff_vote_result';
            for(const match of (e.content||'').matchAll(/(\d+)号→(\d+)号|(\d+)号弃票/g)) {
                const voter=Number(match[1]||match[3]),target=match[2]?Number(match[2]):null;
                votes[voter]=target;
                if(target!==null) tally[target]=(tally[target]||0)+(!sheriffVote&&voter===historySheriff?1.5:1);
            }
            addVoteCard({votes,tally,summary:sheriffVote?'投票结束':(e.content||'').split('（投票详情')[0]},sheriffVote?'警长投票':'放逐投票');
        } else if (e.content) {
            const key=['vote_result','sheriff_vote_result','death_announce','seer_result','witch_save','witch_poison','sheriff_transfer','sheriff_elected','hunter_shot','idiot_reveal','wolf_explode'].includes(e.event_type);
            let text=e.content;
            if(e.event_type==='seer_result') text=`仅你可见 · 第${e.round}夜查验 ${e.target_seat}号：${e.content}`;
            const kind=e.event_type==='death_announce'?'highlight-death':e.event_type==='wolf_explode'?'highlight-explode':key?'highlight-vote':'system';
            addLog(text,e.phase,kind);
        } else if(['witch_save','witch_poison'].includes(e.event_type)) {
            addLog(`仅你可见 · ${e.event_type==='witch_save'?'解药救了':'毒药使用于'} ${e.target_seat}号`,e.phase,'highlight-save');
        }
    }
    _currentRound=savedRound;
    if(following) jumpToLatest(); else container.scrollTop=oldTop;
}
function notesKey() { return `ww_notes_${getUsername()}_${gameId}`; }
function loadNotes() {
    try {
        const value=JSON.parse(localStorage.getItem(notesKey())||'{}');
        _notes={text:typeof value.text==='string'?value.text:'', marks: value.marks && typeof value.marks==='object'?value.marks:{}};
    } catch (_) { _notes={text:'',marks:{}}; }
    $('#private-note').value=_notes.text;
}
function saveNotes() {
    try {localStorage.setItem(notesKey(),JSON.stringify(_notes));$('#note-saved').textContent='已保存于本机';}
    catch(_){$('#note-saved').textContent='本机存储不可用，关闭页面后笔记将丢失';}
}
function inspectPlayer(seat) {
    _inspectedSeat=seat;
    const p=players.find(p=>p.seat===seat);if(!p)return;
    const last=[..._speechRecords].reverse().find(e=>e.seat===seat);
    const mark=_notes.marks[seat]||'未知';
    const el=$('#player-inspector');
    el.innerHTML=`<div class="inspector-card"><div class="inspector-name">${escapeHTML(getDisplayName(seat))}</div><div class="inspector-meta">${p.is_alive?'存活':'已出局'}${seat===sheriffSeat?' · 警长':''}${p.role?' · '+escapeHTML(ROLE_NAMES[p.role]):''}${p.model_name?'<br>'+escapeHTML(p.model_name):''}</div><div class="mark-options">${['未知','偏好','偏狼','神职'].map(m=>`<button class="${mark===m?'active':''}" data-mark="${m}">${m}</button>`).join('')}</div><div class="inspector-quote">${last?`最近发言 · 第 ${last.round} 轮<br>${escapeHTML(last.content)}`:'还没有听到 TA 的发言。'}</div></div>`;
    el.querySelectorAll('[data-mark]').forEach(btn=>btn.onclick=()=>{_notes.marks[seat]=btn.dataset.mark;saveNotes();inspectPlayer(seat);renderSeats();});
    document.querySelectorAll('.seat').forEach(el=>el.classList.toggle('selected',el.id===`seat-${seat}`));
}
function toggleNotebook(force) {
    const panel=document.querySelector('.notebook-panel');
    const wasOpen=panel.classList.contains('open');
    const open=force===undefined?!wasOpen:force;
    if(open&&!wasOpen) _notebookTrigger=document.activeElement;
    $('#notebook-backdrop').classList.toggle('hidden',!open);
    panel.classList.toggle('open',open);$('#mobile-notes-button').setAttribute('aria-expanded',String(open));
    $('#mobile-notes-button').textContent=open?'✕ 收起推理簿':'✎ 推理簿';
    if(open&&!wasOpen) panel.querySelector('.notebook-close').focus();
    if(!open&&wasOpen) {
        if(_notebookTrigger?.isConnected) _notebookTrigger.focus();
        _notebookTrigger=null;
    }
}
function updateSpeechCount() { $('#speech-count').textContent=`${$('#input-text').value.length} / 500 · Ctrl / ⌘ + Enter 发送`; }
function startActionClock() {
    clearInterval(_actionClock);
    const update=()=>{
        if(!_pendingPrompt?.expires_at) {$('#action-deadline').textContent='';return;}
        const seconds=Math.max(0,Math.ceil(_pendingPrompt.expires_at-Date.now()/1000));
        $('#action-deadline').textContent=`剩余 ${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
    };
    update();_actionClock=setInterval(update,1000);
}
function showRules() { $('#rules-overlay').classList.remove('hidden');$('#rules-overlay button').focus(); }
function hideRules() { $('#rules-overlay').classList.add('hidden'); }
$('#input-text').addEventListener('input',updateSpeechCount);
$('#input-text').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();$('#input-submit').click();}});
$('#private-note').addEventListener('input',()=>{_notes.text=$('#private-note').value;saveNotes();});
$('#log-content').addEventListener('scroll',()=>{
    const el=$('#log-content');
    _followLogs=el.scrollHeight-el.scrollTop-el.clientHeight<85;
    if(_followLogs) $('#new-messages').classList.add('hidden');
});
// Keep the latest speech visible when the action area or viewport changes size.
new ResizeObserver(()=>{
    if(_followLogs&&$('#game-screen').classList.contains('active')) jumpToLatest();
}).observe($('#log-content'));
new ResizeObserver(entries=>{
    const stage=entries[0];
    if(!stage.contentRect.width || !stage.contentRect.height) return;
    // Keep a square in short desktop windows; scroll the board if necessary.
    const size=Math.max(400, Math.floor(Math.min(stage.contentRect.width, stage.contentRect.height)));
    stage.target.style.setProperty('--table-size', `${size}px`);
    stage.target.classList.toggle('table-scrollable', stage.contentRect.height < size);
}).observe($('#seats-container'));
document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){hideRules();toggleNotebook(false);}
    const panel=document.querySelector('.notebook-panel');
    if(e.key==='Tab'&&panel.classList.contains('open')) {
        const items=panel.querySelectorAll('button:not(:disabled), textarea');
        const first=items[0],last=items[items.length-1];
        if(!panel.contains(document.activeElement)){e.preventDefault();(e.shiftKey?last:first).focus();}
        else if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
        else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
    }
});

// ── Page init ──
(async function init() {
    try {
        const response=await fetch('/api/runtime');
        if(response.ok) {
            const runtime=await response.json();
            _runtimeMode=runtime.ai_mode;
            const preview=_runtimeMode==='simulated';
            document.body.classList.toggle('preview-mode',preview);
            $('#runtime-banner').classList.toggle('hidden',!preview);
        }
    } catch (_) { /* Old/offline services still reach the login screen. */ }
    // Enter key for login
    const pwdInput = $("#login-password");
    if (pwdInput) {
        pwdInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") login();
        });
    }

    const token = getToken();
    if (!token) {
        show("login-screen");
        return;
    }

    // Verify token
    try {
        const res = await fetch("/api/auth/me", { headers: authHeaders() });
        if (!res.ok) {
            clearToken();
            show("login-screen");
            return;
        }
        const user = await res.json();
        setNickname(user.nickname);
        setUsername(user.username);
        setAdminStatus(user.is_admin);
        await onLoginSuccess();
    } catch (e) {
        clearToken();
        show("login-screen");
    }
})();
