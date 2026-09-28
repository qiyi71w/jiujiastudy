/**
 * 救驾 Web 交互前端应用脚本
 *
 * 遵循 CSP 规范（无内联脚本、无内联事件、无内联样式）。
 * 纯 DOM 构建与 textContent 文本绑定，防范 XSS 注入。
 * 严格校验 URL：仅允许与当前 Canvas 站点同源且无 userinfo 的 HTTPS 链接。
 * 敏感数据不持久化到 localStorage（仅保存浅深主题配置与退出意图布尔标记，严禁持久化令牌与凭据）。
 */
(function () {
  'use strict';

  // --- 运行时状态 ---
  let currentUser = null;
  let csrfToken = null;
  let currentState = null;
  let activeTab = 'today';

  // 筛选器状态
  let taskCourseFilter = '';
  let taskStateFilter = 'todo'; // 'todo' | 'completed' | 'all'
  let announcementCourseFilter = '';
  let announcementReadFilter = 'unread'; // 'unread' | 'read' | 'all'

  // 并发与会话生命周期锁（不阻塞退出）
  let isSubmitting = false;
  let isLoggingOut = false;
  let sessionEpoch = 0;

  // DOM 元素引用
  let elAriaStatus = null;
  let elViewLogin = null;
  let elLoginForm = null;
  let elLoginUsername = null;
  let elLoginPassword = null;
  let elLoginError = null;
  let elLoginSubmit = null;

  let elViewApp = null;
  let elHeaderAccount = null;
  let elHeaderTimestamp = null;
  let elHeaderSaving = null;
  let elBtnTheme = null;
  let elBtnLogout = null;

  let elDesktopNav = null;
  let elMobileNav = null;
  let elBannerRegion = null;
  let elAppToast = null;
  let elToastText = null;

  let elTabToday = null;
  let elTabTasks = null;
  let elTabAnnouncements = null;
  let elTabSettings = null;

  let toastTimer = null;
  let countdownTimer = null;
  let aiProgress = null;

  function courseLabel(course) {
    const code = String(course.code || '').trim();
    const name = String(course.name || '').trim();
    if (!code) return name;
    const suffix = name.slice(code.length);
    if (name.toLowerCase().startsWith(code.toLowerCase()) && (!suffix || /^[\s·:：—–-]/u.test(suffix))) return name;
    return name ? `${code} · ${name}` : code;
  }

  function clearAiProgress() {
    if (!aiProgress) return;
    if (aiProgress.eventSource) {
      aiProgress.eventSource.close();
      aiProgress.eventSource = null;
    }
    aiProgress.finished = true;
    if (aiProgress.dialog) {
      aiProgress.dialog.remove();
    }
    aiProgress = null;
  }

  function startAiProgress(requestId) {
    clearAiProgress();
    const dialog = el('dialog', 'ai-progress');
    dialog.setAttribute('aria-labelledby', 'ai-progress-title');
    const title = el('h2', '', '正在生成 AI 解读');
    title.id = 'ai-progress-title';
    const model = el('p', 'item-meta', '模型：正在确认');
    const stage = el('p', 'report-content', '准备请求');
    stage.setAttribute('role', 'status');
    const metrics = el('p', 'item-meta');

    const previewBox = el('div', 'ai-preview');
    previewBox.hidden = true;
    const previewHeader = el('div', 'ai-preview-header');
    const previewBadge = tagBadge('生成中·尚未校验', 'warn');
    const previewWarning = el('span', 'ai-preview-warning', '完整校验后才保存解读；候选行动仍需本人确认');
    previewHeader.append(previewBadge, previewWarning);
    const previewBody = el('div', 'ai-preview-body report-content');
    previewBody.setAttribute('role', 'region');
    previewBody.setAttribute('aria-label', 'AI 解读草稿预览');
    previewBody.tabIndex = 0;
    previewBox.append(previewHeader, previewBody);

    const close = el('button', 'btn btn-secondary', '收起（仍会继续生成）');
    close.type = 'button';
    close.addEventListener('click', () => dialog.close());

    dialog.append(title, model, stage, metrics, previewBox, close);
    document.body.appendChild(dialog);
    dialog.showModal();

    const job = {
      dialog,
      model,
      stage,
      metrics,
      previewBox,
      previewBadge,
      previewWarning,
      previewBody,
      title,
      close,
      eventSource: null,
      session: csrfToken,
      finished: false,
      status: 'queued',
      clearPreview() {
        previewBox.hidden = true;
        previewBody.textContent = '';
      }
    };
    aiProgress = job;

    const labels = {
      queued: '准备请求',
      collecting: '正在更新 Canvas 数据',
      requesting: '正在等待首字输出',
      streaming: '正在生成',
      validating: '正在校验模型输出',
      done: '解读已保存',
      failed: '生成失败，请查看页面提示',
      cancelled: '状态已变更，本次结果未保存',
      skipped: '本次未调用模型'
    };

    job.update = progress => {
      if (aiProgress !== job || csrfToken !== job.session) return;
      if (job.finished && progress.status !== 'done') return;
      job.status = progress.status;
      if (progress.model) model.textContent = '模型：' + progress.model;
      stage.textContent = labels[progress.status] || '正在处理';
      stage.setAttribute('data-status', progress.status || '');
      dialog.setAttribute('data-status', progress.status || '');
      metrics.textContent = `耗时 ${Math.floor(progress.elapsed_seconds || 0)} 秒`;
      if (Number.isFinite(progress.tokens_per_second) && progress.output_tokens > 0) {
        metrics.textContent += ` · 输出 ${progress.output_tokens} tokens · 请求平均 ${progress.tokens_per_second} token/s`;
      }
      if (progress.status === 'failed' || progress.status === 'cancelled' || progress.status === 'skipped') {
        job.clearPreview();
      } else if (typeof progress.preview === 'string' && progress.preview.length > 0) {
        previewBox.hidden = false;
        if (progress.status === 'done') {
          previewBadge.textContent = '已保存';
          previewBadge.className = 'tag good';
          previewWarning.hidden = true;
        } else {
          previewBadge.textContent = '生成中·尚未校验';
          previewBadge.className = 'tag warn';
          previewWarning.hidden = false;
        }
        if (previewBody.textContent !== progress.preview) {
          const prevScroll = previewBody.scrollTop;
          previewBody.textContent = progress.preview;
          previewBody.scrollTop = prevScroll;
        }
      } else if (!progress.preview) {
        job.clearPreview();
      }
    };

    if (typeof EventSource !== 'undefined') {
      try {
        const es = new EventSource('/api/ai-progress/' + encodeURIComponent(requestId));
        job.eventSource = es;
        const onProgressEvent = e => {
          if (aiProgress !== job || csrfToken !== job.session || job.finished) {
            es.close();
            if (job.eventSource === es) job.eventSource = null;
            return;
          }
          try {
            const data = JSON.parse(e.data);
            job.update(data);
            if (data.status === 'done' || data.status === 'failed' || data.status === 'cancelled' || data.status === 'skipped') {
              es.close();
              if (job.eventSource === es) job.eventSource = null;
            }
          } catch (err) {
            /* 忽略非 JSON 数据 */
          }
        };
        es.addEventListener('progress', onProgressEvent);
        es.onerror = () => {
          es.close();
          if (job.eventSource === es) job.eventSource = null;
          if (aiProgress !== job || csrfToken !== job.session || job.finished || job.status === 'done') return;
          stage.textContent = '实时预览连接已断开，请求仍在后台处理中…';
        };
      } catch (err) {
        /* EventSource 初始化异常不阻断 POST 请求 */
      }
    }
    return job;
  }

  // --- 辅助构建函数 ---
  function el(tag, className, textContent) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (textContent !== undefined && textContent !== null) node.textContent = textContent;
    return node;
  }

  function tagBadge(text, kind) {
    return el('span', 'tag ' + (kind || ''), text);
  }

  // --- 安全链接校验 ---
  function isSafeCanvasUrl(rawUrl, canvasOrigin) {
    if (!rawUrl || typeof rawUrl !== 'string' || !canvasOrigin) return false;
    try {
      const u = new URL(rawUrl);
      if (u.protocol !== 'https:') return false;
      if (u.origin !== canvasOrigin) return false;
      if (u.username !== '' || u.password !== '') return false;
      return true;
    } catch (e) {
      return false;
    }
  }

  function createSafeLink(rawUrl, canvasOrigin, text) {
    if (isSafeCanvasUrl(rawUrl, canvasOrigin)) {
      const a = document.createElement('a');
      a.href = rawUrl;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = text || rawUrl;
      return a;
    }
    const span = document.createElement('span');
    span.textContent = text || rawUrl || '';
    return span;
  }

  // --- 时间与格式化 ---
  function formatDateTime(isoString, timeZone, options) {
    if (!isoString) return '时间未定';
    try {
      const date = new Date(isoString);
      if (isNaN(date.getTime())) return String(isoString);
      const tz = timeZone || (currentState && currentState.timezone) || 'Asia/Shanghai';
      const opts = options || {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      };
      return new Intl.DateTimeFormat('zh-CN', Object.assign({ timeZone: tz }, opts)).format(date);
    } catch (e) {
      return String(isoString);
    }
  }

  // --- 反馈与无障碍播报 ---
  function announceAria(msg) {
    if (elAriaStatus) {
      elAriaStatus.textContent = msg;
    }
  }

  function showBanner(msg, type) {
    if (!elBannerRegion) return;
    if (!msg) {
      elBannerRegion.hidden = true;
      elBannerRegion.textContent = '';
      return;
    }
    elBannerRegion.className = 'banner ' + (type || 'info');
    elBannerRegion.textContent = msg;
    elBannerRegion.hidden = false;
    announceAria(msg);
  }

  function hideBanner() {
    if (elBannerRegion) {
      elBannerRegion.hidden = true;
      elBannerRegion.textContent = '';
    }
  }

  function showToast(msg) {
    if (!elAppToast || !elToastText) return;
    elToastText.textContent = msg;
    elAppToast.hidden = false;
    announceAria(msg);
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      elAppToast.hidden = true;
    }, 3000);
  }

  function setSaving(saving) {
    isSubmitting = saving;
    if (elHeaderSaving) {
      elHeaderSaving.hidden = !saving;
    }
    if (elViewApp) {
      const btns = elViewApp.querySelectorAll('button:not(#btn-logout):not(#btn-theme)');
      btns.forEach(btn => {
        if (saving) {
          btn.dataset.disabledBeforeSave = String(btn.disabled);
          btn.disabled = true;
        } else if ('disabledBeforeSave' in btn.dataset) {
          btn.disabled = btn.dataset.disabledBeforeSave === 'true';
          delete btn.dataset.disabledBeforeSave;
        }
      });
    }
  }

  // --- 主题管理（仅主题允许存储于 localStorage） ---
  function initTheme() {
    let saved = null;
    try {
      saved = localStorage.getItem('coach_theme');
    } catch (e) {}
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = saved || (prefersDark ? 'dark' : 'light');
    applyTheme(theme);
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem('coach_theme', theme);
    } catch (e) {}
    if (elBtnTheme) {
      elBtnTheme.textContent = theme === 'dark' ? '浅色' : '深色';
      elBtnTheme.setAttribute('aria-label', theme === 'dark' ? '切到浅色主题' : '切到深色主题');
    }
  }

  function toggleTheme() {
    const cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    applyTheme(cur === 'dark' ? 'light' : 'dark');
  }

  // --- 网络请求核心 ---
  async function apiRequest(endpoint, options) {
    const requestSession = csrfToken;
    const requestEpoch = sessionEpoch;
    const opts = options || {};
    const headers = Object.assign({}, opts.headers || {});
    if (opts.body && typeof opts.body === 'string' && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    if (csrfToken && opts.method && opts.method.toUpperCase() !== 'GET') {
      headers['X-CSRF-Token'] = csrfToken;
    }

    const fetchOpts = Object.assign({}, opts, {
      headers,
      credentials: 'same-origin'
    });

    const res = await fetch(endpoint, fetchOpts);
    if (csrfToken !== requestSession || sessionEpoch !== requestEpoch) {
      const err = new Error('会话已切换，忽略旧请求结果');
      err.stale = true;
      throw err;
    }

    if (res.status === 401) {
      handleUnauthorized();
      const err = new Error('会话已失效，请重新登录');
      err.status = 401;
      err.serverUnauthorized = true;
      throw err;
    }

    if (res.status === 409) {
      showBanner('操作冲突：服务端数据已被其他操作修改，正在拉取最新状态...', 'warn');
      await fetchStateSilently();
      const err = new Error('409');
      err.status = 409;
      throw err;
    }

    let data;
    try {
      data = await res.json();
    } catch (e) {
      data = {};
    }

    if (csrfToken !== requestSession || sessionEpoch !== requestEpoch) {
      const err = new Error('会话已切换，忽略旧请求结果');
      err.stale = true;
      throw err;
    }

    if (!res.ok) {
      const err = new Error(data.error || '请求失败，请稍后重试');
      err.status = res.status;
      err.data = data;
      throw err;
    }

    return data;
  }

  // --- 鉴权与会话生命周期 ---
  const LOGOUT_PENDING_KEY = 'coach_logout_pending';
  let logoutPending = false;

  function setLogoutPending(val) {
    logoutPending = Boolean(val);
    try {
      if (val) {
        localStorage.setItem(LOGOUT_PENDING_KEY, 'true');
      } else {
        localStorage.removeItem(LOGOUT_PENDING_KEY);
      }
      return true;
    } catch (e) {
      return false;
    }
  }

  function isLogoutPending() {
    try {
      if (localStorage.getItem(LOGOUT_PENDING_KEY) === 'true') {
        return true;
      }
    } catch (e) {}
    return logoutPending;
  }

  function handleUnauthorized() {
    sessionEpoch++;
    setLogoutPending(false);
    currentUser = null;
    csrfToken = null;
    currentState = null;
    clearAppDom();
    elViewApp.hidden = true;
    elViewLogin.hidden = false;
    showLoginError('登录已失效，请重新登录');
  }

  function clearAppDom() {
    clearAiProgress();
    setSaving(false);
    if (elTabToday) elTabToday.replaceChildren();
    if (elTabTasks) elTabTasks.replaceChildren();
    if (elTabAnnouncements) elTabAnnouncements.replaceChildren();
    if (elTabSettings) elTabSettings.replaceChildren();
    if (elHeaderAccount) elHeaderAccount.textContent = '';
    if (elHeaderTimestamp) elHeaderTimestamp.textContent = '';
    hideBanner();
  }

  async function checkSession() {
    if (isLogoutPending()) {
      // 存在待退出意图：立即隐藏敏感页面，刷新先尝试撤销再决定显示，不可静默恢复登录
      sessionEpoch++;
      currentUser = null;
      csrfToken = null;
      currentState = null;
      clearAppDom();
      showLoginView();

      let sessionData = null;
      try {
        sessionData = await apiRequest('/api/session', { method: 'GET' });
      } catch (e) {
        sessionData = null;
      }

      if (!sessionData) {
        showLoginView();
        showLoginError('退出未完成：网络异常，无法连接服务端；已阻止自动登录，请检查网络');
        return;
      }

      if (!sessionData.authenticated) {
        // 服务端会话已失效或已注销
        setLogoutPending(false);
        clearLoginError();
        showLoginView();
        showToast('已安全退出');
        return;
      }

      // 服务端会话仍有效，必须尝试撤销
      csrfToken = sessionData.csrf || sessionData.csrf_token || null;
      let revoked = false;
      let alreadyInvalid = false;
      try {
        await apiRequest('/api/logout', { method: 'POST', body: JSON.stringify({}) });
        revoked = true;
      } catch (err) {
        if (err && err.status === 401 && !err.stale && err.serverUnauthorized) {
          alreadyInvalid = true;
        }
      } finally {
        csrfToken = null;
      }

      if (revoked || alreadyInvalid) {
        setLogoutPending(false);
        clearLoginError();
        showLoginView();
        showToast('已安全退出');
      } else {
        showLoginView();
        showLoginError('退出未完成：网络异常，服务端会话尚未注销；已阻止自动登录，请检查网络');
      }
      return;
    }

    try {
      const res = await apiRequest('/api/session', { method: 'GET' });
      if (res && res.authenticated) {
        sessionEpoch++;
        currentUser = res.username || null;
        csrfToken = res.csrf || res.csrf_token || null;
        showAppView();
        await fetchState();
      } else {
        showLoginView();
      }
    } catch (e) {
      showLoginView();
    }
  }

  function showLoginView() {
    elViewApp.hidden = true;
    elViewLogin.hidden = false;
    if (elLoginUsername) elLoginUsername.focus();
  }

  function showAppView() {
    elViewLogin.hidden = true;
    elViewApp.hidden = false;
  }

  function showLoginError(msg) {
    if (!elLoginError) return;
    elLoginError.textContent = msg || '用户名或密码错误，请重试';
    elLoginError.hidden = false;
    announceAria(elLoginError.textContent);
  }

  function clearLoginError() {
    if (elLoginError) {
      elLoginError.hidden = true;
      elLoginError.textContent = '';
    }
  }

  async function handleLogin(e) {
    e.preventDefault();
    if (isLoggingOut) return;
    clearLoginError();
    const username = elLoginUsername.value.trim();
    const password = elLoginPassword.value;
    if (!username || !password) return;

    // 若存在待退出会话，先通过既有 checkSession 恢复/撤销路径处理旧会话
    if (isLogoutPending()) {
      elLoginSubmit.disabled = true;
      elLoginSubmit.textContent = '清理旧会话中...';
      try {
        await checkSession();
      } finally {
        elLoginSubmit.disabled = false;
        elLoginSubmit.textContent = '登录';
      }
      if (isLogoutPending()) {
        // 仍未成功撤销旧会话，阻止登录并保留准确提示
        if (elLoginPassword) elLoginPassword.focus();
        return;
      }
    }

    elLoginSubmit.disabled = true;
    elLoginSubmit.textContent = '登录中...';

    try {
      const res = await apiRequest('/api/login', {
        method: 'POST',
        body: JSON.stringify({ username, password })
      });
      sessionEpoch++;
      setLogoutPending(false);
      currentUser = res.username || username;
      csrfToken = res.csrf || res.csrf_token || null;
      if (elLoginPassword) elLoginPassword.value = '';
      clearLoginError();
      showAppView();
      await fetchState();
    } catch (err) {
      if (!err || !err.stale) {
        showLoginError('用户名或密码错误，请重试');
        if (elLoginPassword) elLoginPassword.focus();
      }
    } finally {
      elLoginSubmit.disabled = false;
      elLoginSubmit.textContent = '登录';
    }
  }

  async function handleLogout(e) {
    if (e && typeof e.preventDefault === 'function') {
      e.preventDefault();
    }
    if (isLoggingOut) return;
    isLoggingOut = true;
    if (elLoginSubmit) elLoginSubmit.disabled = true;

    // 退出操作不被 isSubmitting 阻塞
    sessionEpoch++;
    const storageOk = setLogoutPending(true);

    // 立即隐藏敏感页面并清理内存状态
    currentUser = null;
    currentState = null;
    clearAppDom();
    showLoginView();
    if (elLoginPassword) elLoginPassword.value = '';
    clearLoginError();

    let revoked = false;
    let alreadyInvalid = false;

    try {
      await apiRequest('/api/logout', { method: 'POST', body: JSON.stringify({}) });
      revoked = true;
    } catch (err) {
      if (err && err.status === 401 && !err.stale && err.serverUnauthorized) {
        alreadyInvalid = true;
      }
    } finally {
      csrfToken = null;
      isLoggingOut = false;
      if (elLoginSubmit) elLoginSubmit.disabled = false;
    }
    if (revoked || alreadyInvalid) {
      setLogoutPending(false);
      clearLoginError();
      showToast('已安全退出');
    } else {
      if (storageOk) {
        showLoginError('退出未完全完成：网络异常，服务端会话可能未注销；已标记待退出，刷新后将尝试自动撤销');
        showToast('退出未完成：网络异常');
      } else {
        showLoginError('退出未完全完成：网络异常且本地存储不可用，服务端会话可能未注销；请勿在公共设备离开');
        showToast('退出未完成：存储不可用');
      }
    }
  }

  // --- 数据获取与操作执行 ---
  async function fetchState() {
    const epoch = sessionEpoch;
    try {
      const state = await apiRequest('/api/state', { method: 'GET' });
      if (epoch !== sessionEpoch || !currentUser || isLogoutPending()) return;
      currentState = state;
      renderAll();
    } catch (e) {
      if (epoch !== sessionEpoch || !currentUser || isLogoutPending()) return;
      if (e.status !== 401 && e.status !== 409) {
        showBanner('获取数据失败：' + (e.message || '网络异常'), 'error');
      }
    }
  }

  async function fetchStateSilently() {
    const epoch = sessionEpoch;
    try {
      const state = await apiRequest('/api/state', { method: 'GET' });
      if (epoch !== sessionEpoch || !currentUser || isLogoutPending()) return;
      currentState = state;
      renderAll();
    } catch (e) {
      // 静默拉取失败不弹打扰窗
    }
  }

  async function sendAction(actionObj) {
    if (isSubmitting) return;
    const session = csrfToken;
    let job = null;
    if (actionObj.action === 'ai') {
      actionObj.request_id = crypto.randomUUID();
      job = startAiProgress(actionObj.request_id);
    }
    setSaving(true);
    hideBanner();
    try {
      const res = await apiRequest('/api/action', {
        method: 'POST',
        body: JSON.stringify(actionObj)
      });
      if (csrfToken !== session) return;
      if (res && res.state) currentState = res.state;
      if (job && res.progress) job.update(res.progress);
      if (res && res.result && res.result.text) showToast(res.result.text);
      renderAll();
    } catch (e) {
      if (csrfToken !== session) return;
      if (job && aiProgress === job) {
        job.clearPreview();
        job.stage.textContent = '请求未完成，请查看页面提示；不要重复提交。';
        job.stage.setAttribute('data-status', 'failed');
        job.dialog.setAttribute('data-status', 'failed');
      }
      if (e.status !== 409 && e.status !== 401) {
        showBanner('操作失败：' + (e.message || '未知错误'), 'error');
      }
    } finally {
      if (job) {
        job.finished = true;
        if (job.eventSource) {
          job.eventSource.close();
          job.eventSource = null;
        }
        job.title.textContent = 'AI 解读请求';
        job.close.textContent = '关闭';
      }
      if (csrfToken === session) setSaving(false);
    }
  }

  // --- 导航与视图切换 ---
  function switchTab(tabName) {
    activeTab = tabName;
    const desktopBtns = elDesktopNav ? elDesktopNav.querySelectorAll('.tab-btn') : [];
    desktopBtns.forEach(btn => {
      const isCur = btn.getAttribute('data-tab') === tabName;
      btn.classList.toggle('is-active', isCur);
      btn.setAttribute('aria-selected', isCur ? 'true' : 'false');
    });

    const mobileBtns = elMobileNav ? elMobileNav.querySelectorAll('.tab-btn') : [];
    mobileBtns.forEach(btn => {
      const isCur = btn.getAttribute('data-tab') === tabName;
      btn.classList.toggle('is-active', isCur);
      btn.setAttribute('aria-selected', isCur ? 'true' : 'false');
    });

    const panels = [
      { name: 'today', el: elTabToday },
      { name: 'tasks', el: elTabTasks },
      { name: 'announcements', el: elTabAnnouncements },
      { name: 'settings', el: elTabSettings }
    ];

    panels.forEach(p => {
      if (p.el) {
        p.el.hidden = p.name !== tabName;
      }
    });

    renderActiveTab();
    window.scrollTo({ top: 0, behavior: 'instant' });
  }

  // --- 整体与分 Tab 渲染 ---
  function renderAll() {
    if (!currentState || !currentUser || isLogoutPending()) return;
    renderHeader();
    renderActiveTab();
  }

  function renderHeader() {
    if (!currentState) return;
    if (elHeaderAccount) {
      const accountText = currentState.account
        ? (currentState.account.label || currentState.account.id || currentUser)
        : currentUser;
      elHeaderAccount.textContent = accountText ? `（${accountText}）` : '';
    }
    if (elHeaderTimestamp) {
      elHeaderTimestamp.textContent = currentState.collected_at
        ? '数据截至 ' + formatDateTime(currentState.collected_at, currentState.timezone)
        : '尚无采集数据';
    }
  }

  function renderActiveTab() {
    if (!currentState) return;
    switch (activeTab) {
      case 'today':
        renderTabToday();
        break;
      case 'tasks':
        renderTabTasks();
        break;
      case 'announcements':
        renderTabAnnouncements();
        break;
      case 'settings':
        renderTabSettings();
        break;
    }
  }

  function studyItem(item, controls = true) {
    const row = el('div', 'study-item');
    const heading = el('div', 'study-item-heading');
    heading.appendChild(el('span', 'code', item.course));
    heading.appendChild(createSafeLink(item.url, currentState.account.canvas_origin,
      (item.activity === 'review' ? '复习：' : '') + item.title));
    if (item.completed) heading.appendChild(tagBadge('已完成', 'good'));
    if (item.pinned) heading.appendChild(tagBadge('本人固定', ''));
    if (item.needs_review) heading.appendChild(tagBadge('变更待复核', 'warn'));
    row.appendChild(heading);
    if (item.first_step) row.appendChild(el('p', 'study-first-step', '第一步：' + item.first_step));
    const meta = [item.source_label];
    if (item.due_at) meta.push('截止 ' + formatDateTime(item.due_at));
    if (item.minutes) meta.push(`${item.minutes} 分钟（估算）`);
    if (item.weight && item.weight !== '—') meta.push(`权重 ${item.weight}（${item.weight_source || '来源待核对'}）`);
    row.appendChild(el('p', 'item-meta', meta.filter(Boolean).join(' · ')));
    if (item.due_at && !item.completed) {
      const countdown = el('span', 'task-countdown');
      countdown.setAttribute('data-due-at', item.due_at);
      row.appendChild(countdown);
    }
    if (!controls || !item.version) return row;
    const actions = el('div', 'item-actions');
    const done = el('button', 'btn btn-secondary btn-sm', item.completed ? '恢复待办' :
      item.task_id && item.activity !== 'review' ? '完成并停催' : '完成这一项');
    done.type = 'button';
    done.addEventListener('click', () => sendAction({ action: 'study', id: item.id, version: item.version,
      value: { operation: item.completed ? 'reopen' : 'complete' } }));
    actions.appendChild(done);
    row.appendChild(actions);
    const edit = el('details', 'study-edit');
    edit.appendChild(el('summary', '', '调整日期与第一步'));
    const form = el('form', 'study-edit-form');
    const dateLabel = el('label', '', '安排日期（不修改截止时间）');
    const date = el('input', 'form-input');
    date.type = 'date';
    date.min = currentState.weekly_plan.week;
    date.max = currentState.weekly_plan.end;
    date.value = item.date || currentState.weekly_plan.today;
    date.required = true;
    dateLabel.appendChild(date);
    const stepLabel = el('label', '', '我的第一步');
    const step = el('textarea', 'form-input');
    step.value = item.first_step || '';
    step.maxLength = 500;
    step.required = true;
    stepLabel.appendChild(step);
    const save = el('button', 'btn btn-primary btn-sm', '保存并固定本周安排');
    save.type = 'submit';
    form.append(dateLabel, stepLabel, save);
    form.addEventListener('submit', event => {
      event.preventDefault();
      sendAction({ action: 'study', id: item.id, version: item.version,
        value: { operation: 'schedule', date: date.value, first_step: step.value } });
    });
    if (item.pinned) {
      const unpin = el('button', 'btn btn-ghost btn-sm', '恢复自动安排');
      unpin.type = 'button';
      unpin.addEventListener('click', () => sendAction({ action: 'study', id: item.id, version: item.version, value: { operation: 'unpin' } }));
      form.appendChild(unpin);
    }
    edit.appendChild(form);
    row.appendChild(edit);
    return row;
  }

  function announcementActions(announcementId, inCurrentSnapshot = true) {
    const section = el('div', 'announcement-actions');
    const items = (currentState.weekly_plan?.announcement_actions || []).filter(a => a.announcement_id === announcementId);
    items.forEach(item => {
      const row = el('div', 'study-item');
      row.appendChild(el('h4', '', item.title));
      const status = item.needs_review ? '公告正文已变更，需重新核对' :
        { pending: 'AI 候选，待本人确认', confirmed: '已确认，纳入安排', completed: '已完成', dismissed: '已搁置' }[item.status];
      row.appendChild(tagBadge(status, item.needs_review || item.status === 'pending' ? 'warn' : ''));
      row.appendChild(el('p', 'study-first-step', '第一步：' + item.first_step));
      row.appendChild(el('p', 'item-meta', item.due_at ? '公告候选时间：' + formatDateTime(item.due_at) : '公告未给出可确认的截止时刻'));
      row.appendChild(el('p', 'item-meta', item.uncertainty));
      row.appendChild(el('blockquote', 'study-evidence', item.evidence));
      row.appendChild(el('p', 'item-meta', `提取于 ${formatDateTime(item.extracted_at)}`));
      const buttons = el('div', 'item-actions');
      const add = (label, value, confirmFirst = false) => {
        const button = el('button', 'btn btn-secondary btn-sm', label);
        button.type = 'button';
        button.addEventListener('click', () => {
          if (confirmFirst && !window.confirm('确认已核对当前公告原文，且此行动已处理完毕？仅在本站记录，不更改公告已读状态，也不会写入 Canvas。')) return;
          sendAction({ action: 'announcement_action', id: item.id, version: item.action_version, value });
        });
        buttons.appendChild(button);
      };
      if (item.needs_review || ['pending', 'dismissed'].includes(item.status)) add('已核对原文，加入计划', 'confirm');
      if (inCurrentSnapshot && (item.needs_review || ['pending', 'dismissed'].includes(item.status))) add('已处理完毕', 'complete_reviewed', true);
      else if (!item.needs_review && !['pending', 'dismissed'].includes(item.status)) add(item.status === 'completed' ? '恢复待办' : '完成行动', item.status === 'completed' ? 'reopen' : 'complete');
      if (item.status !== 'dismissed') add('搁置此行动', 'dismiss');
      row.appendChild(buttons);
      if (!inCurrentSnapshot && (item.needs_review || ['pending', 'dismissed'].includes(item.status))) {
        row.appendChild(el('p', 'item-meta', '当前采集范围外：刷新后核对当前公告原文，才能直接标记已处理。'));
      }
      section.appendChild(row);
    });
    return section;
  }

  function renderStudyPlan() {
    const plan = currentState.weekly_plan;
    if (!plan) return;
    const section = el('section', 'study-plan');
    section.id = 'study-plan';
    const layout = el('div', 'study-layout');
    const primary = el('div', 'study-primary');
    primary.appendChild(el('h2', 'section-title', '今天先做这一件'));
    primary.appendChild(el('p', 'item-meta', `${plan.today} · ${plan.timezone} · 按已采集资料安排`));
    const today = plan.days.find(day => day.date === plan.today);
    if (today?.must) primary.appendChild(studyItem(today.must));
    else primary.appendChild(el('p', 'empty-hint', '今天没有已安排的必做；可从本周重点或待确认事项中选择。'));
    const week = el('div', 'study-week');
    week.appendChild(el('h2', 'section-title', '本周重点'));
    if (plan.top.length) {
      const list = el('ol', 'study-week-list');
      plan.top.slice(0, 3).forEach(item => {
        const li = el('li');
        const body = el('div');
        body.appendChild(createSafeLink(item.url, currentState.account.canvas_origin, `${item.course} · ${item.title}`));
        const meta = [item.date, item.due_at && '截止 ' + formatDateTime(item.due_at), item.minutes && `${item.minutes} 分钟（估算）`].filter(Boolean);
        if (meta.length) body.appendChild(el('p', 'item-meta', meta.join(' · ')));
        const detail = el('details', 'study-edit');
        detail.appendChild(el('summary', '', '查看具体安排与原文'));
        detail.appendChild(studyItem(item, true));
        body.appendChild(detail);
        li.appendChild(body);
        list.appendChild(li);
      });
      week.appendChild(list);
    } else week.appendChild(el('p', 'empty-hint', '当前没有可从来源确定的本周重点。'));
    layout.append(primary, week);
    section.appendChild(layout);
    const secondary = el('div', 'study-secondary');
    if (today?.should.length) {
      const optional = el('details', 'fold');
      optional.appendChild(el('summary', '', `有余力再做（${today.should.length} 项）`));
      today.should.forEach(item => optional.appendChild(studyItem(item)));
      secondary.appendChild(optional);
    }
    const pendingActions = plan.announcement_actions.filter(a => a.status === 'pending' || a.needs_review);
    if (pendingActions.length) {
      const notice = el('button', 'btn btn-secondary section-gap', `${pendingActions.length} 项公告行动待确认`);
      notice.type = 'button';
      notice.addEventListener('click', () => { announcementReadFilter = 'all'; announcementCourseFilter = ''; switchTab('announcements'); renderTabAnnouncements(); });
      secondary.appendChild(notice);
    }
    const calendar = el('details', 'fold study-calendar');
    calendar.appendChild(el('summary', '', `七日安排 · ${plan.week} 至 ${plan.end}`));
    plan.days.forEach(day => {
      const group = el('section', 'study-day');
      group.appendChild(el('h4', '', day.date + (day.date === plan.today ? ' · 今天' : '')));
      if (day.must) group.appendChild(studyItem(day.must));
      day.should.forEach(item => group.appendChild(studyItem(item)));
      day.completed.forEach(item => group.appendChild(studyItem(item)));
      if (!day.must && !day.should.length && !day.completed.length) group.appendChild(el('p', 'item-meta', '暂无安排'));
      calendar.appendChild(group);
    });
    secondary.appendChild(calendar);
    if (plan.clashes.length || plan.overloaded_dates.length) {
      const clashes = el('div', 'callout section-gap');
      clashes.appendChild(el('strong', '', '撞期提醒'));
      plan.clashes.forEach(group => clashes.appendChild(el('p', '', group.map(i => `${i.course} ${i.title}（${formatDateTime(i.due_at)}）`).join('；'))));
      if (plan.overloaded_dates.length) clashes.appendChild(el('p', '', '安排超出每日容量：' + plan.overloaded_dates.join('、') + '；其余事项保留在先搁着。'));
      secondary.appendChild(clashes);
    }
    if (plan.pending.length || plan.gaps.length) {
      const pending = el('details', 'fold');
      pending.appendChild(el('summary', '', `日期与资料待确认（${plan.pending.length + plan.gaps.length} 项）`));
      plan.pending.forEach(item => pending.appendChild(studyItem(item, false)));
      plan.gaps.forEach(gap => {
        const p = el('p', 'item-meta');
        p.appendChild(createSafeLink(gap.source, currentState.account.canvas_origin, `${gap.course}：${gap.text}`));
        if (gap.collected_at) p.appendChild(el('span', '', ' · 数据截至 ' + formatDateTime(gap.collected_at)));
        pending.appendChild(p);
      });
      secondary.appendChild(pending);
    }
    if (plan.parking.length) {
      const parking = el('details', 'fold');
      parking.appendChild(el('summary', '', `先搁着 / 待安排（${plan.parking.length} 项）`));
      plan.parking.forEach(item => parking.appendChild(studyItem(item)));
      secondary.appendChild(parking);
    }
    section.appendChild(secondary);
    elTabToday.appendChild(section);
  }

  // ==========================================
  // 近期任务实时倒计时
  // ==========================================
  function updateTaskCountdowns() {
    const nodes = document.querySelectorAll('.task-countdown[data-due-at]');
    if (!nodes || nodes.length === 0) return;
    const now = Date.now();

    nodes.forEach(node => {
      const dueStr = node.getAttribute('data-due-at');
      if (!dueStr) {
        if (node.textContent !== '截止时间未知') node.textContent = '截止时间未知';
        if (node.className !== 'task-countdown is-normal') node.className = 'task-countdown is-normal';
        return;
      }
      const dueTime = new Date(dueStr).getTime();
      if (isNaN(dueTime)) {
        if (node.textContent !== '截止时间未知') node.textContent = '截止时间未知';
        if (node.className !== 'task-countdown is-normal') node.className = 'task-countdown is-normal';
        return;
      }

      const diff = dueTime - now;
      if (diff <= 0) {
        if (node.textContent !== '已逾期') node.textContent = '已逾期';
        if (node.className !== 'task-countdown is-overdue') node.className = 'task-countdown is-overdue';
        return;
      }

      const totalSeconds = Math.floor(diff / 1000);
      const days = Math.floor(totalSeconds / 86400);
      const remSeconds = totalSeconds % 86400;
      const hours = Math.floor(remSeconds / 3600);
      const minutes = Math.floor((remSeconds % 3600) / 60);

      let durationText = '';
      if (days > 0) {
        durationText = `${days} 天 ${hours} 小时`;
      } else if (hours > 0) {
        durationText = `${hours} 小时 ${minutes} 分钟`;
      } else if (minutes > 0) {
        durationText = `${minutes} 分钟`;
      } else {
        durationText = '不足 1 分钟';
      }

      let stateClass = 'is-normal';
      if (diff <= 24 * 3600 * 1000) {
        stateClass = 'is-urgent';
      } else if (diff <= 72 * 3600 * 1000) {
        stateClass = 'is-warn';
      } else {
        stateClass = 'is-normal';
      }

      const fullText = durationText === '不足 1 分钟' ? '剩余不足 1 分钟' : `剩余 ${durationText}`;
      if (node.textContent !== fullText) node.textContent = fullText;
      const fullClassName = 'task-countdown ' + stateClass;
      if (node.className !== fullClassName) node.className = fullClassName;
    });
  }

  // ==========================================
  // Tab 1: 今日 (Today)
  // ==========================================
  const reportHeadings = new Set(['未来七天待交', '逾期未交', '日期不明任务', '新增与改期', '最近已提交（尚未通知）', '新课程与课程变化', '暂停提醒的旧数据']);

  function appendReportEntry(parent, text, canvasOrigin) {
    const entry = el('div', 'report-entry');
    text.split('\n').forEach((line, index) => {
      if (index) entry.appendChild(document.createTextNode('\n'));
      const source = line.trim();
      entry.appendChild(source === line && isSafeCanvasUrl(source, canvasOrigin)
        ? createSafeLink(source, canvasOrigin, '查看 Canvas 原文') : document.createTextNode(line));
    });
    parent.appendChild(entry);
  }

  function renderRuleReport(parent, text, canvasOrigin) {
    const lines = text.split('\n');
    const notes = el('div', 'report-notes');
    const routine = el('details', 'report-group');
    routine.appendChild(el('summary', '', '采集时间与课程明细'));
    const sections = [];
    let section = null;
    lines.forEach(line => {
      if (reportHeadings.has(line.trim())) {
        section = { title: line.trim(), entries: [] };
        sections.push(section);
      } else if (section) {
        if (line.trim()) {
          if (line.startsWith('http') || line.startsWith('发现于：')) {
            const last = section.entries.length - 1;
            if (last >= 0) section.entries[last] += '\n' + line;
            else section.entries.push(line);
          } else section.entries.push(line);
        }
      } else if (line.trim() && line !== 'Canvas 规则日报') {
        if (/^(数据截至：|剩余\/逾期时间计算于：|采集失败：|公告采集失败：|刷新全部失败)/.test(line)) {
          notes.appendChild(el('p', /^(采集失败：|公告采集失败：|刷新全部失败)/.test(line) ? 'is-alert' : '', line));
        } else routine.appendChild(el('p', 'item-meta', line));
      }
    });
    parent.appendChild(notes);
    if (routine.childElementCount > 1) parent.appendChild(routine);
    if (!sections.length) {
      appendReportEntry(parent, text, canvasOrigin);
      return;
    }
    sections.forEach(group => {
      const fold = el('details', 'report-group');
      const empty = group.entries.length === 1 && group.entries[0] === '无';
      const count = empty ? ' · 无' : ['新增与改期', '新课程与课程变化'].includes(group.title) ? ' · 有更新' : ` · ${group.entries.length} 项`;
      fold.appendChild(el('summary', !empty && /逾期|日期不明/.test(group.title) ? 'report-attention' : '', group.title + count));
      group.entries.forEach(entry => appendReportEntry(fold, entry, canvasOrigin));
      parent.appendChild(fold);
    });
    const original = el('details', 'fold');
    original.appendChild(el('summary', '', '查看完整原文'));
    original.appendChild(el('div', 'report-content', text));
    parent.appendChild(original);
  }

  function analysisArchiveLabel(item) {
    if (!item.in_current_snapshot) return '历史存档 · 当前采集范围外';
    if (item.analysis_stale) return '旧版本存档 · 正文需重新核对';
    if (item.effective_read === true) return '已读存档 · 默认不再发送给 AI';
    if (item.effective_read === false) return '当前有效未读解读';
    return '阅读状态待确认的存档';
  }

  function appendAnnouncementAnalysis(parent, item, canvasOrigin) {
    const archived = !(item.in_current_snapshot && !item.analysis_stale && item.effective_read === false);
    const fold = el('details', 'ai-archive');
    fold.open = !archived;
    fold.appendChild(el('summary', '', `${item.course} · ${item.title} — ${analysisArchiveLabel(item)}`));
    const callout = el('div', 'ai-announcement-callout');
    const generated = item.generated_at ? `生成于 ${formatDateTime(item.generated_at, currentState.timezone)}` : '生成时间未记录';
    callout.appendChild(el('p', 'item-meta', generated + ' · 已保存的解读'));
    callout.appendChild(el('p', '', item.analysis));
    if (item.source) {
      const src = el('div', 'item-meta');
      src.appendChild(el('span', '', '来源：'));
      src.appendChild(createSafeLink(item.source, canvasOrigin, '查看 Canvas 原文'));
      callout.appendChild(src);
    }
    fold.appendChild(callout);
    parent.appendChild(fold);
  }

  function quotaDescription(quota) {
    if (!quota) return '每日 AI 额度暂不可用';
    const left = Math.max(0, quota.limit - quota.used);
    const reset = quota.reset_at ? `；${formatDateTime(quota.reset_at, currentState.timezone)}（${currentState.timezone}）重置` : '';
    return `每日 AI 额度：已用 ${quota.used} / ${quota.limit} 次，剩余 ${left} 次${reset}`;
  }

  // --- 学习日历 ---
  let calendarMonth = null;
  const CAL_CATEGORY = {
    assignment: { label: '作业', mark: '●' },
    exam: { label: '考试/测验', mark: '◆' },
    announcement: { label: '公告事项', mark: '▲' },
    other: { label: '其他活动', mark: '■' }
  };
  const CAL_SOURCE = { canvas: 'Canvas', announcement: '公告', syllabus: '大纲' };

  function localTodayIso(timeZone) {
    try {
      return new Intl.DateTimeFormat('en-CA', { timeZone: timeZone || undefined, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
    } catch (e) {
      return new Date().toISOString().slice(0, 10);
    }
  }

  function calendarEventMark(ev) {
    const cat = CAL_CATEGORY[ev.category] || CAL_CATEGORY.other;
    const mark = el('span', 'cal-mark cal-' + (CAL_CATEGORY[ev.category] ? ev.category : 'other'), cat.mark);
    if (ev.status === 'candidate') mark.classList.add('is-candidate');
    if (ev.status === 'done') mark.classList.add('is-done');
    mark.setAttribute('aria-hidden', 'true');
    return mark;
  }

  function renderCalendarCard() {
    const cal = currentState.calendar || { events: [], syllabus: { sources: [] } };
    const events = cal.events || [];
    const tz = currentState.timezone;
    const today = localTodayIso(tz);
    if (!calendarMonth) calendarMonth = today.slice(0, 7);
    const byDate = {};
    const undated = [];
    events.forEach(ev => {
      if (ev.date) (byDate[ev.date] = byDate[ev.date] || []).push(ev);
      else undated.push(ev);
    });

    const card = el('div', 'card section-gap calendar-card');
    const header = el('div', 'section-header cal-header');
    header.appendChild(el('h3', 'section-title', '学习日历'));
    const nav = el('div', 'cal-nav');
    const [year, month] = calendarMonth.split('-').map(Number);
    const prev = el('button', 'btn btn-secondary btn-sm', '‹');
    prev.type = 'button';
    prev.setAttribute('aria-label', '上个月');
    const next = el('button', 'btn btn-secondary btn-sm', '›');
    next.type = 'button';
    next.setAttribute('aria-label', '下个月');
    const todayBtn = el('button', 'btn btn-secondary btn-sm', '本月');
    todayBtn.type = 'button';
    const shift = delta => {
      const d = new Date(Date.UTC(year, month - 1 + delta, 1));
      calendarMonth = d.toISOString().slice(0, 7);
      renderTabToday();
    };
    prev.addEventListener('click', () => shift(-1));
    next.addEventListener('click', () => shift(1));
    todayBtn.addEventListener('click', () => { calendarMonth = today.slice(0, 7); renderTabToday(); });
    nav.appendChild(prev);
    nav.appendChild(el('span', 'cal-title', year + ' 年 ' + month + ' 月'));
    nav.appendChild(next);
    nav.appendChild(todayBtn);
    header.appendChild(nav);
    card.appendChild(header);

    const legend = el('div', 'cal-legend');
    Object.keys(CAL_CATEGORY).forEach(key => {
      const item = el('span', 'cal-legend-item');
      item.appendChild(calendarEventMark({ category: key, status: 'confirmed' }));
      item.appendChild(document.createTextNode(CAL_CATEGORY[key].label));
      legend.appendChild(item);
    });
    const candLegend = el('span', 'cal-legend-item');
    candLegend.appendChild(calendarEventMark({ category: 'other', status: 'candidate' }));
    candLegend.appendChild(document.createTextNode('空心 = 待你确认'));
    legend.appendChild(candLegend);
    card.appendChild(legend);

    const grid = el('div', 'cal-grid');
    grid.setAttribute('role', 'grid');
    ['一', '二', '三', '四', '五', '六', '日'].forEach(d => grid.appendChild(el('div', 'cal-weekday', d)));
    const first = new Date(Date.UTC(year, month - 1, 1));
    const lead = (first.getUTCDay() + 6) % 7;
    const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
    for (let i = 0; i < lead; i++) grid.appendChild(el('div', 'cal-day is-empty'));
    for (let day = 1; day <= days; day++) {
      const iso = calendarMonth + '-' + String(day).padStart(2, '0');
      const list = byDate[iso] || [];
      const cell = el('button', 'cal-day');
      cell.type = 'button';
      if (iso === today) cell.classList.add('is-today');
      if (list.length) cell.classList.add('has-events');
      cell.appendChild(el('span', 'cal-num', String(day)));
      if (list.some(ev => ev.needs_review)) cell.appendChild(el('span', 'cal-alert', '!'));
      const dots = el('span', 'cal-dots');
      const bars = el('span', 'cal-bars');
      list.slice(0, 3).forEach(ev => {
        const bar = el('span', 'cal-bar');
        bar.appendChild(calendarEventMark(ev));
        bar.appendChild(el('span', 'cal-bar-text', ev.title || ''));
        bars.appendChild(bar);
      });
      if (list.length > 3) bars.appendChild(el('span', 'cal-more', '+' + (list.length - 3)));
      list.slice(0, 4).forEach(ev => dots.appendChild(calendarEventMark(ev)));
      cell.appendChild(bars);
      cell.appendChild(dots);
      cell.setAttribute('aria-label', month + '月' + day + '日，' + (list.length ? list.length + ' 项' : '无安排'));
      cell.addEventListener('click', () => openCalendarDay(iso, list));
      grid.appendChild(cell);
    }
    card.appendChild(grid);

    const pending = events.filter(ev => ev.status === 'candidate').length;
    const reviews = events.filter(ev => ev.needs_review).length;
    const summary = el('p', 'cal-summary',
      (pending ? pending + ' 项待确认；' : '') + (reviews ? reviews + ' 项需核对；' : '') +
      '只有 Canvas 作业和你确认过的事项为实心。');
    card.appendChild(summary);

    if (undated.length) {
      const box = el('details', 'cal-undated');
      box.appendChild(el('summary', null, '日期未定（' + undated.length + '）'));
      undated.forEach(ev => box.appendChild(renderCalendarEvent(ev)));
      card.appendChild(box);
    }
    card.appendChild(renderSyllabusPanel(cal.syllabus || { sources: [] }));
    return card;
  }

  function calendarButton(text, handler, primary) {
    const b = el('button', 'btn btn-sm ' + (primary ? 'btn-primary' : 'btn-secondary'), text);
    b.type = 'button';
    b.addEventListener('click', handler);
    return b;
  }

  function renderCalendarEvent(ev) {
    const item = el('div', 'cal-event' + (ev.status === 'candidate' ? ' is-candidate' : '') + (ev.status === 'done' ? ' is-done' : ''));
    const head = el('div', 'cal-event-head');
    head.appendChild(calendarEventMark(ev));
    head.appendChild(el('strong', 'cal-event-title', ev.title || '未命名'));
    item.appendChild(head);
    const meta = [ev.course || '', ev.time ? ev.time : (ev.date ? '全天' : (ev.date_text || '日期未定')),
      '来源：' + (CAL_SOURCE[ev.source] || ev.source),
      ev.status === 'candidate' ? '待确认' : (ev.status === 'done' ? '已完成' : '已确认')].filter(Boolean);
    item.appendChild(el('p', 'cal-event-meta', meta.join(' · ')));
    if (ev.evidence) item.appendChild(el('blockquote', 'cal-evidence', ev.evidence));
    if (ev.uncertainty) item.appendChild(el('p', 'cal-event-meta', '不确定：' + ev.uncertainty));
    if (ev.url) {
      const origin = currentState.account && currentState.account.canvas_origin;
      item.appendChild(isSafeCanvasUrl(ev.url, origin)
        ? createSafeLink(ev.url, origin, '查看原文')
        : el('p', 'cal-event-meta cal-url', '来源链接：' + ev.url));
    }
    const actions = el('div', 'cal-event-actions');
    if (ev.conflict) {
      const c = ev.conflict;
      const warn = el('div', 'cal-conflict');
      warn.appendChild(el('p', null, '⚠ ' + c.reason + '：公告写的是 ' + c.other_date + (c.other_time ? ' ' + c.other_time : '') +
        '，大纲原为 ' + ev.date + (ev.time ? ' ' + ev.time : '') + '。'));
      if (c.evidence) warn.appendChild(el('blockquote', 'cal-evidence', c.evidence));
      item.appendChild(warn);
      actions.appendChild(calendarButton('采用新日期', () => calendarAct(ev, 'syllabus_node', 'reschedule'), true));
      actions.appendChild(calendarButton('保留原日期', () => calendarAct(ev, 'syllabus_node', 'keep')));
    }
    if (ev.source === 'syllabus') {
      if (ev.status === 'candidate') {
        actions.appendChild(calendarButton('加入日历', () => calendarAct(ev, 'syllabus_node', 'confirm'), true));
        actions.appendChild(calendarButton('忽略', () => calendarAct(ev, 'syllabus_node', 'dismiss')));
      }
      const dateInput = el('input', 'cal-date-input');
      dateInput.type = 'date';
      dateInput.value = ev.date || '';
      dateInput.setAttribute('aria-label', '修改日期');
      const timeInput = el('input', 'cal-date-input');
      timeInput.type = 'time';
      timeInput.value = ev.time || '';
      timeInput.setAttribute('aria-label', '修改时间');
      actions.appendChild(dateInput);
      actions.appendChild(timeInput);
      actions.appendChild(calendarButton('改日期', () => calendarAct(ev, 'syllabus_node',
        { operation: 'edit', date: dateInput.value || null, time: timeInput.value || null })));
    } else if (ev.source === 'announcement' && ev.status === 'candidate') {
      actions.appendChild(calendarButton('确认加入', () => calendarAct(ev, 'announcement_action', 'confirm'), true));
      actions.appendChild(calendarButton('忽略', () => calendarAct(ev, 'announcement_action', 'dismiss')));
    }
    if (actions.childNodes.length) item.appendChild(actions);
    return item;
  }

  async function calendarAct(ev, action, value) {
    const dialog = document.getElementById('calendar-dialog');
    if (dialog && dialog.open) dialog.close();
    await sendAction({ action, id: ev.id, value, version: ev.version });
  }

  function openCalendarDay(iso, list) {
    let dialog = document.getElementById('calendar-dialog');
    if (!dialog) {
      dialog = el('dialog', 'cal-dialog');
      dialog.id = 'calendar-dialog';
      dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
      document.body.appendChild(dialog);
    }
    dialog.replaceChildren();
    const head = el('div', 'cal-dialog-head');
    head.appendChild(el('h3', 'section-title', iso));
    const close = calendarButton('关闭', () => dialog.close());
    head.appendChild(close);
    dialog.appendChild(head);
    if (!list.length) dialog.appendChild(el('p', 'empty-state', '这一天没有安排。'));
    list.forEach(ev => dialog.appendChild(renderCalendarEvent(ev)));
    dialog.showModal();
  }

  function renderSyllabusPanel(syllabus) {
    const box = el('details', 'cal-syllabus');
    box.appendChild(el('summary', null, '导入课程大纲（考试日期）'));
    box.appendChild(el('p', 'cal-event-meta', 'AI 会从大纲里找出考试、作业等日期，每项都要你确认后才进日历；每个片段消耗 1 次 AI 额度。扫描版 PDF 暂不支持。'));
    const courses = (currentState.courses || []).filter(c => c.monitored && !c.inactive);
    const form = el('form', 'cal-syllabus-form');
    const select = el('select', 'cal-select');
    select.setAttribute('aria-label', '课程');
    courses.forEach(c => {
      const o = el('option', null, c.name || c.code);
      o.value = c.id;
      select.appendChild(o);
    });
    const file = el('input');
    file.type = 'file';
    file.accept = '.pdf,.txt,application/pdf,text/plain';
    file.setAttribute('aria-label', '大纲文件');
    const link = el('input', 'cal-link-input');
    link.type = 'url';
    link.placeholder = '或粘贴 https 大纲链接';
    link.setAttribute('aria-label', '大纲链接');
    const submit = el('button', 'btn btn-primary btn-sm', '开始识别');
    submit.type = 'submit';
    const aiOff = !currentState.settings || !currentState.settings.ai_enabled;
    if (aiOff || !courses.length) {
      submit.disabled = true;
      submit.title = aiOff ? 'AI 未在设置中启用' : '没有正在监控的课程';
    }
    form.appendChild(select);
    form.appendChild(file);
    form.appendChild(link);
    form.appendChild(submit);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const chosen = file.files && file.files[0];
      if (!chosen && !link.value.trim()) { showBanner('请选择文件或填写链接', 'warn'); return; }
      if (chosen && chosen.size > 5 * 1024 * 1024) { showBanner('文件超过 5MB', 'error'); return; }
      let body;
      if (chosen) {
        body = new FormData();
        body.append('course_id', select.value);
        body.append('file', chosen);
      } else {
        body = JSON.stringify({ course_id: select.value, url: link.value.trim() });
      }
      await uploadSyllabus(body);
    });
    box.appendChild(form);
    (syllabus.sources || []).forEach(src => {
      const row = el('div', 'cal-source');
      row.appendChild(el('span', null, (src.course || '') + ' · ' + (src.name || '大纲') + ' · ' + src.nodes + ' 项'));
      row.appendChild(calendarButton('删除', () => {
        if (window.confirm('删除这份大纲及其所有日历条目？')) {
          sendAction({ action: 'syllabus_source', id: src.id, value: 'delete', version: src.version });
        }
      }));
      box.appendChild(row);
    });
    return box;
  }

  async function uploadSyllabus(body) {
    if (isSubmitting) return;
    const session = csrfToken;
    setSaving(true);
    hideBanner();
    showToast('正在识别大纲，可能需要一分钟…');
    try {
      const res = await apiRequest('/api/syllabus', { method: 'POST', body });
      if (csrfToken !== session) return;
      if (res && res.state) currentState = res.state;
      if (res && res.result && res.result.text) showToast(res.result.text);
      renderAll();
    } catch (e) {
      if (csrfToken !== session) return;
      if (e.status !== 409 && e.status !== 401) showBanner('导入失败：' + (e.message || '未知错误'), 'error');
    } finally {
      if (csrfToken === session) setSaving(false);
    }
  }

  function renderTabToday() {
    if (!elTabToday || !currentState) return;
    elTabToday.replaceChildren();

    const canvasOrigin = currentState.account ? currentState.account.canvas_origin : '';
    renderStudyPlan();

    // 服务与采集状态不抢占今天的任务。
    const statusCard = el('div', 'today-status');
    const statusClasses = {
      '正常': 'is-good',
      '落后': 'is-warn',
      '病了': 'is-warn',
      '卡住': 'is-bad',
      '过载': 'is-bad'
    };
    const stClass = statusClasses[currentState.status] || (currentState.complete ? 'is-good' : 'is-warn');
    const statusBox = el('div', 'today-status ' + stClass);
    const bStatus = el('b', '', currentState.status || '系统就绪');
    statusBox.appendChild(bStatus);
    const whyText = currentState.collected_at
      ? '数据截至 ' + formatDateTime(currentState.collected_at, currentState.timezone)
      : '尚无采集数据';
    statusBox.appendChild(el('span', 'why', whyText));
    statusCard.appendChild(statusBox);

    if (!currentState.service_enabled) {
      const svcCallout = el('div', 'callout end-gap', '服务当前处于暂停状态，可在「设置」中开启每日自动计划。');
      statusCard.appendChild(svcCallout);
    }

    if (!currentState.complete || (currentState.failures && currentState.failures.length > 0)) {
      const failList = (currentState.failures || []).join('、') || '部分课程';
      const failCallout = el('div', 'callout end-gap', '采集不完整：以下课程采集失败：' + failList);
      statusCard.appendChild(failCallout);
    }
    elTabToday.appendChild(statusCard);

    // 2. 快捷操作栏（刷新与 AI 解读）
    const actionBar = el('div', 'today-tools');
    const btnRefresh = el('button', 'btn btn-secondary', '刷新数据');
    btnRefresh.type = 'button';
    btnRefresh.addEventListener('click', () => {
      sendAction({ action: 'refresh' });
    });
    actionBar.appendChild(btnRefresh);

    const inputAiQuestion = el('input', 'form-input');
    inputAiQuestion.type = 'text';
    inputAiQuestion.placeholder = '针对当前学情的可选问题（选填）';
    inputAiQuestion.setAttribute('aria-label', '针对当前学情的可选问题');
    actionBar.appendChild(inputAiQuestion);

    const btnAi = el('button', 'btn btn-secondary', '生成 AI 解读');
    btnAi.type = 'button';
    if (!currentState.settings || !currentState.settings.ai_enabled) {
      btnAi.disabled = true;
      btnAi.title = 'AI 分析功能未在设置中启用';
    }
    btnAi.addEventListener('click', () => {
      const q = inputAiQuestion.value.trim();
      sendAction({ action: 'ai', params: q ? [q] : [] });
    });
    actionBar.appendChild(btnAi);
    actionBar.appendChild(el('p', 'quota-note', quotaDescription(currentState.quota)));
    elTabToday.appendChild(actionBar);
    elTabToday.appendChild(renderCalendarCard());

    // 3. 近期任务（由已有 tasks 筛选日期不猜事实）
    const tasksCard = el('div', 'card section-gap');
    const tasksHeader = el('div', 'section-header');
    tasksHeader.appendChild(el('h3', 'section-title', '近期待办事项'));
    tasksCard.appendChild(tasksHeader);

    const allTasks = currentState.tasks || [];
    const activeTasks = allTasks.filter(t => !t.stopped && (!t.completed || t.needs_review) && !['submitted', 'excused'].includes(t.sub_state));
    activeTasks.sort((a, b) => {
      if (!a.due_at && !b.due_at) return 0;
      if (!a.due_at) return 1;
      if (!b.due_at) return -1;
      return new Date(a.due_at).getTime() - new Date(b.due_at).getTime();
    });

    const recentSlice = activeTasks.slice(0, 10);
    if (recentSlice.length === 0) {
      tasksCard.appendChild(el('p', 'empty-hint', '近期暂无待办事项'));
    } else {
      const ul = el('ul', 'rows nobox');
      recentSlice.forEach(task => {
        const li = el('li');
        li.setAttribute('data-row', '1');

        const colMain = el('div', 't');
        colMain.appendChild(el('span', 'code', task.course));

        const linkNode = createSafeLink(task.source, canvasOrigin, task.name);
        colMain.appendChild(linkNode);

        const badgeGroup = el('div', 'status-group');
        // Canvas 提交状态（unknown 不当未交）
        if (task.sub_state === 'submitted') {
          badgeGroup.appendChild(tagBadge('Canvas: 已提交', 'good'));
        } else if (task.sub_state === 'excused') {
          badgeGroup.appendChild(tagBadge('Canvas: 豁免', 'good'));
        } else if (task.sub_state === 'unknown' || task.needs_review) {
          badgeGroup.appendChild(tagBadge('Canvas: 需确认', 'warn'));
        } else {
          badgeGroup.appendChild(tagBadge('Canvas: 未提交', 'bad'));
        }
        colMain.appendChild(badgeGroup);
        li.appendChild(colMain);

        const colMeta = el('div', 'm');
        colMeta.appendChild(el('span', 'num', formatDateTime(task.due_at, currentState.timezone)));

        const countdownSpan = el('span', 'task-countdown');
        countdownSpan.setAttribute('data-due-at', task.due_at || '');
        colMeta.appendChild(countdownSpan);

        const btnComplete = el('button', 'btn btn-secondary btn-sm', '完成并停催');
        btnComplete.type = 'button';
        btnComplete.addEventListener('click', () => {
          if (window.confirm('确认完成此任务并停止后续提醒？')) {
            sendAction({ action: 'task', id: task.id, value: 'complete', version: task.version });
          }
        });
        colMeta.appendChild(btnComplete);

        li.appendChild(colMeta);
        ul.appendChild(li);
      });
      tasksCard.appendChild(ul);
    }
    elTabToday.appendChild(tasksCard);

    // 4. 规则日报
    const reportCard = el('div', 'card section-gap');
    reportCard.appendChild(el('h3', 'section-title', '规则日报'));
    if (currentState.report) {
      renderRuleReport(reportCard, currentState.report, canvasOrigin);
    } else {
      reportCard.appendChild(el('p', 'empty-hint', '暂无规则日报内容'));
    }
    elTabToday.appendChild(reportCard);

    // 5. 已保存 AI 解读
    const aiCard = el('div', 'card section-gap');
    aiCard.appendChild(el('h3', 'section-title', '已保存 AI 解读'));
    if (currentState.analysis) {
      const analysis = currentState.analysis;
      if (analysis.generated_at) {
        aiCard.appendChild(el('p', 'item-meta', `生成于 ${formatDateTime(analysis.generated_at)}（${currentState.timezone}）`));
      }
      if (analysis.timezone && analysis.timezone !== currentState.timezone) {
        aiCard.appendChild(el('p', 'item-meta', `此份解读正文按 ${analysis.timezone} 生成；下次生成将使用当前时区。`));
      }
      if (currentState.analysis.summary) {
        aiCard.appendChild(el('p', 'report-content', currentState.analysis.summary));
      }
      if (currentState.analysis.next_step) {
        const nextCallout = el('div', 'callout end-gap');
        nextCallout.appendChild(el('strong', '', '下一步建议：'));
        nextCallout.appendChild(el('span', '', currentState.analysis.next_step));
        aiCard.appendChild(nextCallout);
      }
      const annAnalyses = currentState.analysis.announcements || [];
      if (annAnalyses.length > 0) {
        const annSec = el('div', 'section-gap');
        annSec.appendChild(el('h4', 'section-title', '公告要点解读'));
        const archived = el('details', 'fold');
        const current = annAnalyses.filter(item => item.in_current_snapshot && !item.analysis_stale && item.effective_read === false);
        const stored = annAnalyses.filter(item => !(item.in_current_snapshot && !item.analysis_stale && item.effective_read === false));
        current.forEach(item => appendAnnouncementAnalysis(annSec, item, canvasOrigin));
        if (stored.length) {
          archived.appendChild(el('summary', '', `已读、旧版及历史存档（${stored.length} 条）`));
          archived.appendChild(el('p', 'item-meta', '保留供查阅，不代表本次重发；展开后可查看每条生成时间与原文。'));
          stored.forEach(item => appendAnnouncementAnalysis(archived, item, canvasOrigin));
          annSec.appendChild(archived);
        }
        aiCard.appendChild(annSec);
      }
    } else {
      aiCard.appendChild(el('p', 'empty-hint', '暂无已保存 AI 解读，可点击上方「生成 AI 解读」按钮'));
    }
    elTabToday.appendChild(aiCard);

    // 6. 有限历史折叠
    const historyList = currentState.history || [];
    const fold = el('details', 'fold section-gap');
    const summary = el('summary', '', `历史日报与解读（${historyList.length} 条）`);
    fold.appendChild(summary);

    if (historyList.length === 0) {
      fold.appendChild(el('p', 'empty-hint', '暂无历史记录'));
    } else {
      const histUl = el('ul', 'changes');
      historyList.forEach(hist => {
        const li = el('li');
        const entry = el('details', 'ai-archive');
        entry.appendChild(el('summary', '', hist.day || formatDateTime(hist.formed_at, currentState.timezone)));
        if (hist.text) entry.appendChild(el('div', 'gist', hist.text));
        if (hist.ai_text) {
          const aiGist = el('div', 'gist');
          aiGist.appendChild(el('strong', '', 'AI 解读：'));
          aiGist.appendChild(el('span', '', hist.ai_text));
          entry.appendChild(aiGist);
        }
        li.appendChild(entry);
        histUl.appendChild(li);
      });
      fold.appendChild(histUl);
    }
    elTabToday.appendChild(fold);
    updateTaskCountdowns();
  }

  // ==========================================
  // Tab 2: 作业 (Tasks)
  // ==========================================
  function renderTabTasks() {
    if (!elTabTasks || !currentState) return;
    elTabTasks.replaceChildren();

    const canvasOrigin = currentState.account ? currentState.account.canvas_origin : '';

    // 筛选工具栏
    const filterBar = el('div', 'filter-bar');

    // 课程筛选下拉框
    const courseSelect = el('select', 'form-select');
    courseSelect.setAttribute('aria-label', '课程筛选');
    const optAllCourse = el('option', '', '全部课程');
    optAllCourse.value = '';
    courseSelect.appendChild(optAllCourse);

    const courses = currentState.courses || [];
    courses.forEach(c => {
      const opt = el('option', '', courseLabel(c));
      opt.value = c.id;
      if (String(c.id) === String(taskCourseFilter)) {
        opt.selected = true;
      }
      courseSelect.appendChild(opt);
    });
    courseSelect.addEventListener('change', () => {
      taskCourseFilter = courseSelect.value;
      renderTabTasks();
    });
    filterBar.appendChild(courseSelect);

    // 状态分段按钮：待办 / 已完成 / 全部
    const filterGroup = el('div', 'filter-group');
    const stateFilters = [
      { id: 'todo', label: '待办' },
      { id: 'completed', label: '已完成' },
      { id: 'all', label: '全部' }
    ];
    stateFilters.forEach(f => {
      const btn = el('button', 'filter-btn' + (taskStateFilter === f.id ? ' is-active' : ''), f.label);
      btn.type = 'button';
      btn.addEventListener('click', () => {
        taskStateFilter = f.id;
        renderTabTasks();
      });
      filterGroup.appendChild(btn);
    });
    filterBar.appendChild(filterGroup);
    elTabTasks.appendChild(filterBar);

    // 作业列表筛选与排序
    let list = (currentState.tasks || []).slice();
    if (taskCourseFilter) {
      list = list.filter(t => String(t.course_id) === taskCourseFilter);
    }
    if (taskStateFilter === 'todo') {
      list = list.filter(t => (!t.completed || t.needs_review) && !['submitted', 'excused'].includes(t.sub_state));
    } else if (taskStateFilter === 'completed') {
      list = list.filter(t => t.completed && !t.needs_review);
    }

    list.sort((a, b) => {
      if (!a.due_at && !b.due_at) return 0;
      if (!a.due_at) return 1;
      if (!b.due_at) return -1;
      return new Date(a.due_at).getTime() - new Date(b.due_at).getTime();
    });

    if (list.length === 0) {
      elTabTasks.appendChild(el('p', 'empty-hint', '无匹配的作业任务'));
      return;
    }

    const container = el('div', 'item-list');
    list.forEach(task => {
      const card = el('div', 'item-card');

      const header = el('div', 'item-header');
      const titleWrap = el('div');
      titleWrap.appendChild(el('span', 'code', task.course));
      const link = createSafeLink(task.source, canvasOrigin, task.name);
      link.className = 'item-title';
      titleWrap.appendChild(link);
      header.appendChild(titleWrap);
      card.appendChild(header);

      const meta = el('div', 'item-meta');
      meta.appendChild(el('span', '', '截止：' + formatDateTime(task.due_at, currentState.timezone)));
      card.appendChild(meta);

      // 双状态显示：完成 vs 提交（不用完成伪造提交）
      const statusGroup = el('div', 'status-group section-gap');
      // 本地学习完成状态
      if (task.needs_review) {
        statusGroup.appendChild(tagBadge('本地: 完成状态需复核', 'warn'));
      } else if (task.completed) {
        statusGroup.appendChild(tagBadge('本地: 已完成', 'good'));
      } else {
        statusGroup.appendChild(tagBadge('本地: 待完成', 'warn'));
      }
      // Canvas 远端提交状态（状态 unknown 不当未交）
      if (task.sub_state === 'submitted') {
        statusGroup.appendChild(tagBadge('Canvas: 已提交', 'good'));
      } else if (task.sub_state === 'excused') {
        statusGroup.appendChild(tagBadge('Canvas: 豁免', 'good'));
      } else if (task.sub_state === 'unknown' || task.needs_review) {
        statusGroup.appendChild(tagBadge('Canvas: 需确认', 'warn'));
      } else {
        statusGroup.appendChild(tagBadge('Canvas: 未提交', 'bad'));
      }
      // 提醒状态
      if (task.stopped) {
        statusGroup.appendChild(tagBadge('提醒: 已停止', 'warn'));
      } else {
        statusGroup.appendChild(tagBadge('提醒: 正常', 'good'));
      }
      card.appendChild(statusGroup);

      // 操作按钮组
      const actions = el('div', 'item-actions');

      if (!task.completed || task.needs_review) {
        const btnComplete = el('button', 'btn btn-secondary btn-sm', '完成并停止提醒');
        btnComplete.type = 'button';
        btnComplete.addEventListener('click', () => {
          if (window.confirm('确认将此任务标记为已完成并停止提醒？')) {
            sendAction({ action: 'task', id: task.id, value: 'complete', version: task.version });
          }
        });
        actions.appendChild(btnComplete);
      } else {
        const btnReopen = el('button', 'btn btn-secondary btn-sm', '恢复待办');
        btnReopen.type = 'button';
        btnReopen.addEventListener('click', () => {
          sendAction({ action: 'task', id: task.id, value: 'reopen', version: task.version });
        });
        actions.appendChild(btnReopen);
      }

      // 独立 stop / resume
      if (task.stopped) {
        const btnResume = el('button', 'btn btn-ghost btn-sm', '恢复提醒');
        btnResume.type = 'button';
        btnResume.addEventListener('click', () => {
          sendAction({ action: 'task', id: task.id, value: 'resume', version: task.version });
        });
        actions.appendChild(btnResume);
      } else {
        const btnStop = el('button', 'btn btn-ghost btn-sm', '停止提醒');
        btnStop.type = 'button';
        btnStop.addEventListener('click', () => {
          sendAction({ action: 'task', id: task.id, value: 'stop', version: task.version });
        });
        actions.appendChild(btnStop);
      }

      card.appendChild(actions);
      container.appendChild(card);
    });
    elTabTasks.appendChild(container);
  }

  // ==========================================
  // Tab 3: 公告 (Announcements)
  // ==========================================
  function renderTabAnnouncements() {
    if (!elTabAnnouncements || !currentState) return;
    elTabAnnouncements.replaceChildren();

    const canvasOrigin = currentState.account ? currentState.account.canvas_origin : '';

    // 1. 公告范围与完整性展示
    const range = currentState.announcement_range || {};
    const rangeCard = el('div', 'range-card');
    const startStr = range.start ? formatDateTime(range.start, currentState.timezone) : '起始未定';
    const endStr = range.end ? formatDateTime(range.end, currentState.timezone) : '当前';
    rangeCard.appendChild(el('div', '', `公告收集范围：${startStr} 至 ${endStr}`));
    if (range.complete === false) {
      rangeCard.appendChild(el('div', 'tag bad', '收集不完整：部分课程公告可能未抓取'));
    }
    elTabAnnouncements.appendChild(rangeCard);

    // 2. 筛选栏（默认未读）
    const filterBar = el('div', 'filter-bar');

    const courseSelect = el('select', 'form-select');
    courseSelect.setAttribute('aria-label', '课程筛选');
    const optAll = el('option', '', '全部课程');
    optAll.value = '';
    courseSelect.appendChild(optAll);

    (currentState.courses || []).forEach(c => {
      const opt = el('option', '', courseLabel(c));
      opt.value = c.id;
      if (String(c.id) === String(announcementCourseFilter)) {
        opt.selected = true;
      }
      courseSelect.appendChild(opt);
    });
    courseSelect.addEventListener('change', () => {
      announcementCourseFilter = courseSelect.value;
      renderTabAnnouncements();
    });
    filterBar.appendChild(courseSelect);

    const filterGroup = el('div', 'filter-group');
    const readFilters = [
      { id: 'unread', label: '未读' },
      { id: 'read', label: '已读' },
      { id: 'all', label: '全部' }
    ];
    readFilters.forEach(f => {
      const btn = el('button', 'filter-btn' + (announcementReadFilter === f.id ? ' is-active' : ''), f.label);
      btn.type = 'button';
      btn.addEventListener('click', () => {
        announcementReadFilter = f.id;
        renderTabAnnouncements();
      });
      filterGroup.appendChild(btn);
    });
    filterBar.appendChild(filterGroup);
    elTabAnnouncements.appendChild(filterBar);

    // 3. 列表过滤
    let list = (currentState.announcements || []).slice();
    if (announcementCourseFilter) {
      list = list.filter(a => String(a.course_id) === announcementCourseFilter);
    }
    if (announcementReadFilter === 'unread') {
      list = list.filter(a => a.effective_read === false);
    } else if (announcementReadFilter === 'read') {
      list = list.filter(a => a.effective_read === true);
    }

    list.sort((a, b) => {
      if (!a.posted_at && !b.posted_at) return 0;
      if (!a.posted_at) return 1;
      if (!b.posted_at) return -1;
      return new Date(b.posted_at).getTime() - new Date(a.posted_at).getTime();
    });
    const currentIds = new Set((currentState.announcements || []).map(a => a.id));
    const historical = (currentState.weekly_plan?.announcement_actions || []).filter(a =>
      !currentIds.has(a.announcement_id) && (!announcementCourseFilter || a.course_id === announcementCourseFilter));
    if (historical.length) {
      const preserved = el('section', 'card section-gap');
      preserved.appendChild(el('h3', 'section-title', '采集范围外的持续行动'));
      const seen = new Set();
      historical.forEach(item => {
        if (seen.has(item.announcement_id)) return;
        seen.add(item.announcement_id);
        preserved.appendChild(createSafeLink(item.source, canvasOrigin, item.course + ' · 公告原文'));
        preserved.appendChild(announcementActions(item.announcement_id, false));
      });
      elTabAnnouncements.appendChild(preserved);
    }
    const archivedAnalyses = (currentState.analysis?.announcements || []).filter(a =>
      !a.in_current_snapshot && (!announcementCourseFilter || String(a.course_id) === announcementCourseFilter));
    if (archivedAnalyses.length) {
      const archive = el('details', 'fold section-gap');
      archive.appendChild(el('summary', '', `采集范围外的已保存公告解读（${archivedAnalyses.length} 条）`));
      archivedAnalyses.forEach(item => appendAnnouncementAnalysis(archive, item, canvasOrigin));
      elTabAnnouncements.appendChild(archive);
    }

    if (list.length === 0) {
      elTabAnnouncements.appendChild(el('p', 'empty-hint', '无匹配的公告内容'));
      return;
    }

    const container = el('div', 'item-list');
    list.forEach(ann => {
      const card = el('div', 'item-card');

      const header = el('div', 'item-header');
      const titleWrap = el('div');
      titleWrap.appendChild(el('span', 'code', ann.course));
      const link = createSafeLink(ann.source, canvasOrigin, ann.title);
      link.className = 'item-title';
      titleWrap.appendChild(link);
      header.appendChild(titleWrap);
      card.appendChild(header);

      const meta = el('div', 'item-meta');
      meta.appendChild(el('span', '', '发布时间：' + formatDateTime(ann.posted_at, currentState.timezone)));
      card.appendChild(meta);

      // 状态标签组
      const statusGroup = el('div', 'status-group section-gap');
      if (ann.canvas_read_state === 'read') {
        statusGroup.appendChild(tagBadge('Canvas: 已读', 'good'));
      } else if (ann.canvas_read_state === 'unread') {
        statusGroup.appendChild(tagBadge('Canvas: 未读', 'warn'));
      } else {
        statusGroup.appendChild(tagBadge('Canvas: 状态待确认', 'warn'));
      }

      if (ann.local_read) {
        statusGroup.appendChild(tagBadge('本地: 已读', 'good'));
      } else {
        statusGroup.appendChild(tagBadge('本地: 未读', 'warn'));
      }

      if (ann.effective_read === true) {
        statusGroup.appendChild(tagBadge('综合: 已读', 'good'));
      } else if (ann.effective_read === false) {
        statusGroup.appendChild(tagBadge('综合: 未读', 'warn'));
      } else {
        statusGroup.appendChild(tagBadge('综合: 待确认', 'warn'));
      }
      card.appendChild(statusGroup);
      statusGroup.appendChild(tagBadge(ann.analyzed ? '当前版本已分析' : ann.analysis_stale ? '正文变更，旧解读待复核' : '尚未分析', ann.analyzed ? '' : 'warn'));

      // 本地已读操作栏与明确说明
      const actions = el('div', 'item-actions');
      const btnToggleRead = el('button', 'btn btn-secondary btn-sm', ann.local_read ? '标为本地未读' : '标为本地已读');
      btnToggleRead.type = 'button';
      btnToggleRead.addEventListener('click', () => {
        sendAction({
          action: 'announcement',
          id: ann.id,
          value: !ann.local_read,
          version: ann.version
        });
      });
      actions.appendChild(btnToggleRead);

      const noteText = (ann.canvas_read_state === 'read' && ann.local_read)
        ? '提示：Canvas 远端已为已读，本地标记为未读仅供本站管理，无法撤销 Canvas 远端已读状态。'
        : '提示：本地标记已读不写回 Canvas。';
      actions.appendChild(el('span', 'meta', noteText));
      card.appendChild(actions);

      // 正文折叠
      const fold = el('details', 'fold');
      fold.appendChild(el('summary', '', '展开公告正文'));
      fold.appendChild(el('div', 'item-body', ann.text || '正文为空'));
      card.appendChild(fold);
      if (!ann.analyzed) {
        const consent = el('details', 'fold');
        consent.appendChild(el('summary', '', '分析这条并加入计划'));
        consent.appendChild(el('p', 'item-meta', '仅将这条当前版本公告正文发送给已配置模型一次，包括已读内容；使用一次 AI 额度，不更改长期授权。提取结果仍需本人确认后才加入安排。'));
        const analyze = el('button', 'btn btn-primary btn-sm', '确认单条授权并分析');
        analyze.type = 'button';
        analyze.disabled = !currentState.settings.ai_enabled || !currentState.service_enabled;
        if (analyze.disabled) consent.appendChild(el('p', 'item-meta', '请先开启账号服务与 AI 主开关。'));
        analyze.addEventListener('click', () => sendAction({ action: 'ai', id: ann.id, version: ann.version, value: 'analyze_once' }));
        consent.appendChild(analyze);
        card.appendChild(consent);
      }
      card.appendChild(announcementActions(ann.id));

      // 对应 AI 解读紧跟来源
      if (currentState.analysis && currentState.analysis.announcements) {
        const matched = currentState.analysis.announcements.find(a => a.id === ann.id || a.source === ann.source);
        if (matched) appendAnnouncementAnalysis(card, matched, canvasOrigin);
      }

      container.appendChild(card);
    });
    elTabAnnouncements.appendChild(container);
  }

  // ==========================================
  // Tab 4: 设置 (Settings)
  // ==========================================
  function renderTabSettings() {
    if (!elTabSettings || !currentState) return;
    elTabSettings.replaceChildren();

    // 1. 服务总开关
    const serviceCard = el('div', 'settings-card');
    serviceCard.appendChild(el('h3', '', '服务运行状态'));
    const serviceRow = el('div', 'setting-row');
    const svcInfo = el('div', 'setting-info');
    svcInfo.appendChild(el('div', 'setting-title', '定时提醒与扫描服务'));
    svcInfo.appendChild(el('div', 'setting-desc', '开启后每日按计划采集并生成日报；是否发送 Telegram 由通知开关控制。关闭后停止自动扫描与新采集。'));
    serviceRow.appendChild(svcInfo);

    const btnService = el(
      'button',
      currentState.service_enabled ? 'btn btn-secondary' : 'btn btn-primary',
      currentState.service_enabled ? '服务运行中（点击暂停）' : '服务已暂停（点击开启）'
    );
    btnService.type = 'button';
    btnService.addEventListener('click', () => {
      sendAction({ action: 'service', value: !currentState.service_enabled });
    });
    serviceRow.appendChild(btnService);
    serviceCard.appendChild(serviceRow);
    elTabSettings.appendChild(serviceCard);

    // 2. 5 项布尔开关
    const boolCard = el('div', 'settings-card');
    boolCard.appendChild(el('h3', '', '提醒与 AI 设置'));

    const toggles = [
      { id: 'ai_enabled', title: '启用 AI 分析功能', desc: 'AI 功能总开关。关闭时仍可正常查看与管理所有公告。' },
      { id: 'ai_summary', title: '每日自动生成 AI 日报摘要', desc: '在每日定时任务中随规则日报一同生成 AI 摘要。' },
      { id: 'ai_announcements', title: '允许 AI 分析公告内容', desc: '授权 AI 对课程公告正文进行解析与要点提炼。' },
      { id: 'announcement_collection', title: '启用公告收集', desc: '采集各监控课程的最新公告列表。' },
      { id: 'telegram_notifications', title: '启用 Telegram 通知', desc: '发送每日日报；关闭后网站仍更新，手动 Bot 命令仍会回复。' }
    ];

    const currentSettings = currentState.settings || {};
    toggles.forEach(t => {
      const row = el('div', 'setting-row');
      const info = el('div', 'setting-info');
      const toggleLabel = el('label', 'setting-title', t.title);
      toggleLabel.htmlFor = 'setting-' + t.id;
      info.appendChild(toggleLabel);
      info.appendChild(el('div', 'setting-desc', t.desc));

      // TG 未绑定限制
      if (t.id === 'telegram_notifications' && !currentState.telegram_available) {
        info.appendChild(el('div', 'tag warn', 'Telegram 专属通道未配置（未绑定），不可开启通知'));
      }
      row.appendChild(info);

      const checkbox = el('input');
      checkbox.type = 'checkbox';
      checkbox.id = 'setting-' + t.id;
      checkbox.checked = !!currentSettings[t.id];

      if (t.id === 'telegram_notifications' && !currentState.telegram_available) {
        checkbox.disabled = true;
      }

      checkbox.addEventListener('change', () => {
        sendAction({
          action: 'setting',
          id: t.id,
          value: checkbox.checked,
          version: currentState.settings_version
        });
      });

      row.appendChild(checkbox);
      boolCard.appendChild(row);
    });
    elTabSettings.appendChild(boolCard);

    // 3. 监控课程设置
    const courseCard = el('div', 'settings-card');
    courseCard.appendChild(el('h3', '', '监控课程管理'));
    courseCard.appendChild(el('p', 'setting-desc', '已停止监控的课程不会自动催交；新发现课程会自动纳入监控。'));

    const courseList = currentState.courses || [];
    if (courseList.length === 0) {
      courseCard.appendChild(el('p', 'empty-hint', '暂无监控课程数据'));
    } else {
      courseList.forEach(course => {
        const item = el('div', 'course-list-item');
        const info = el('div');
        info.appendChild(el('strong', '', courseLabel(course)));
        if (course.inactive) {
          info.appendChild(tagBadge('已归档', 'warn'));
        }
        item.appendChild(info);

        const btn = el('button', course.monitored ? 'btn btn-ghost btn-sm' : 'btn btn-secondary btn-sm', course.monitored ? '停止监控' : '加入监控');
        btn.type = 'button';
        btn.addEventListener('click', () => {
          sendAction({
            action: 'course',
            id: course.id,
            value: !course.monitored,
            version: course.version
          });
        });
        item.appendChild(btn);
        courseCard.appendChild(item);
      });
    }
    elTabSettings.appendChild(courseCard);

    // 4. 每日计划与时区设置
    const schedCard = el('div', 'settings-card');
    schedCard.appendChild(el('h3', '', '每日计划时间与时区'));

    const schedForm = el('div', 'grid-2col');
    const fTime = el('div', 'form-field');
    fTime.appendChild(el('label', '', '每日计划时间（HH:MM）'));
    const inputTime = el('input', 'form-input');
    inputTime.type = 'time';
    inputTime.id = 'schedule-time';
    fTime.querySelector('label').htmlFor = inputTime.id;
    inputTime.value = currentSettings.daily_time || '08:00';
    fTime.appendChild(inputTime);
    schedForm.appendChild(fTime);

    const fTz = el('div', 'form-field');
    fTz.appendChild(el('label', '', '时区（IANA 标识）'));
    const inputTz = el('input', 'form-input');
    inputTz.type = 'text';
    inputTz.id = 'schedule-timezone';
    fTz.querySelector('label').htmlFor = inputTz.id;
    inputTz.value = currentSettings.daily_timezone || currentState.timezone || 'Asia/Shanghai';
    fTz.appendChild(inputTz);
    schedForm.appendChild(fTz);

    schedCard.appendChild(schedForm);

    const btnSaveSched = el('button', 'btn btn-primary', '保存调度设置');
    btnSaveSched.type = 'button';
    btnSaveSched.addEventListener('click', () => {
      const timeVal = inputTime.value.trim();
      const tzVal = inputTz.value.trim();
      if (!timeVal) {
        showBanner('请输入每日计划时间', 'error');
        return;
      }
      sendAction({ action: 'schedule', params: [timeVal, tzVal], version: currentState.settings_version });
    });
    schedCard.appendChild(btnSaveSched);
    elTabSettings.appendChild(schedCard);

    // 5. AI 调用额度展示
    const quotaCard = el('div', 'settings-card');
    quotaCard.appendChild(el('h3', '', '每日 AI 额度'));
    quotaCard.appendChild(el('p', 'setting-desc', quotaDescription(currentState.quota)));
    if (currentState.quota?.period_start && currentState.quota?.reset_at) {
      const quota = currentState.quota;
      quotaCard.appendChild(el('p', 'setting-desc', `本次额度周期：${formatDateTime(quota.period_start, currentState.timezone)} 至 ${formatDateTime(quota.reset_at, currentState.timezone)}（${currentState.timezone}）。按固定 ${quota.timezone} 每日零点重置，修改显示时区不重置额度。`));
    }
    quotaCard.appendChild(el('p', 'setting-desc', '自动日报、手动生成与单条公告分析共用同一每日上限。'));
    elTabSettings.appendChild(quotaCard);

    // 6. 密码变更（成功后强制安全退出）
    const pwdCard = el('div', 'settings-card');
    pwdCard.appendChild(el('h3', '', '修改密码'));
    const pwdForm = el('form');

    const fCurPwd = el('div', 'form-field');
    fCurPwd.appendChild(el('label', '', '当前密码'));
    const inputCurPwd = el('input', 'form-input');
    inputCurPwd.type = 'password';
    inputCurPwd.id = 'current-password';
    fCurPwd.querySelector('label').htmlFor = inputCurPwd.id;
    inputCurPwd.autocomplete = 'current-password';
    inputCurPwd.required = true;
    fCurPwd.appendChild(inputCurPwd);
    pwdForm.appendChild(fCurPwd);

    const fNewPwd = el('div', 'form-field');
    fNewPwd.appendChild(el('label', '', '新密码'));
    const inputNewPwd = el('input', 'form-input');
    inputNewPwd.type = 'password';
    inputNewPwd.id = 'new-password';
    fNewPwd.querySelector('label').htmlFor = inputNewPwd.id;
    inputNewPwd.autocomplete = 'new-password';
    inputNewPwd.required = true;
    fNewPwd.appendChild(inputNewPwd);
    pwdForm.appendChild(fNewPwd);

    const fConfirmPwd = el('div', 'form-field');
    fConfirmPwd.appendChild(el('label', '', '确认新密码'));
    const inputConfirmPwd = el('input', 'form-input');
    inputConfirmPwd.type = 'password';
    inputConfirmPwd.id = 'confirm-password';
    fConfirmPwd.querySelector('label').htmlFor = inputConfirmPwd.id;
    inputConfirmPwd.autocomplete = 'new-password';
    inputConfirmPwd.required = true;
    fConfirmPwd.appendChild(inputConfirmPwd);
    pwdForm.appendChild(fConfirmPwd);

    const btnChangePwd = el('button', 'btn btn-primary', '确认修改密码');
    btnChangePwd.type = 'submit';
    pwdForm.appendChild(btnChangePwd);

    pwdForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const curPwd = inputCurPwd.value;
      const newPwd = inputNewPwd.value;
      const confirmPwd = inputConfirmPwd.value;

      if (!newPwd) {
        showBanner('新密码不能为空', 'error');
        return;
      }
      if (newPwd.length < 12 || newPwd.length > 256) {
        showBanner('新密码长度须在12至256个字符之间', 'error');
        return;
      }
      if (newPwd !== confirmPwd) {
        showBanner('新密码与确认密码不一致', 'error');
        return;
      }

      setSaving(true);
      try {
        await apiRequest('/api/password', {
          method: 'POST',
          body: JSON.stringify({ current_password: curPwd, new_password: newPwd })
        });
        // 成功修改密码后退出登录
        sessionEpoch++;
        setLogoutPending(false);
        currentUser = null;
        csrfToken = null;
        currentState = null;
        clearAppDom();
        elViewApp.hidden = true;
        elViewLogin.hidden = false;
        showToast('密码修改成功，请使用新密码重新登录');
      } catch (err) {
        showBanner('修改密码失败：' + (err.message || '当前密码不正确'), 'error');
      } finally {
        setSaving(false);
      }
    });

    pwdCard.appendChild(pwdForm);
    elTabSettings.appendChild(pwdCard);
  }

  // --- 初始化绑定 ---
  function init() {
    elAriaStatus = document.getElementById('aria-status');
    elViewLogin = document.getElementById('view-login');
    elLoginForm = document.getElementById('login-form');
    elLoginUsername = document.getElementById('login-username');
    elLoginPassword = document.getElementById('login-password');
    elLoginError = document.getElementById('login-error');
    elLoginSubmit = document.getElementById('login-submit');

    elViewApp = document.getElementById('view-app');
    elHeaderAccount = document.getElementById('header-account');
    elHeaderTimestamp = document.getElementById('header-timestamp');
    elHeaderSaving = document.getElementById('header-saving');
    elBtnTheme = document.getElementById('btn-theme');
    elBtnLogout = document.getElementById('btn-logout');

    elDesktopNav = document.querySelector('.desktop-nav');
    elMobileNav = document.querySelector('.mobile-nav');
    elBannerRegion = document.getElementById('banner-region');
    elAppToast = document.getElementById('app-toast');
    elToastText = document.getElementById('toast-text');

    elTabToday = document.getElementById('tab-today');
    elTabTasks = document.getElementById('tab-tasks');
    elTabAnnouncements = document.getElementById('tab-announcements');
    elTabSettings = document.getElementById('tab-settings');

    // 绑定基础事件
    if (elLoginForm) {
      elLoginForm.addEventListener('submit', handleLogin);
    }
    if (elBtnLogout) {
      elBtnLogout.addEventListener('click', handleLogout);
    }
    if (elBtnTheme) {
      elBtnTheme.addEventListener('click', toggleTheme);
    }

    const tabButtons = document.querySelectorAll('.tab-btn');
    tabButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        if (tab) switchTab(tab);
      });
    });

    // 页面 visibility 回前台刷新本地 API（不触发 Canvas 采集或 AI）并立即更新倒计时
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        updateTaskCountdowns();
        if (currentUser && !isSubmitting) {
          fetchStateSilently();
        }
      }
    });

    initTheme();
    if (!countdownTimer) {
      countdownTimer = setInterval(() => {
        updateTaskCountdowns();
        if (!currentUser || isSubmitting || !currentState?.weekly_plan) return;
        const date = new Intl.DateTimeFormat('en-CA', { timeZone: currentState.timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
        if (date !== currentState.weekly_plan.today) fetchStateSilently();
      }, 30000);
    }
    checkSession();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
