"""救驾 Web UI 渲染与样式模块

导出 WebUI 类：
- page() -> str: 返回完整 SPA 容器 HTML，不含 inline 脚本与样式，满足 CSP 规范。
- stylesheet() -> str: 组合 design.TOKENS、design.BASE 与 Web 交互端局部 CSS。
"""
try:
    from .design import TOKENS, BASE
except ImportError:
    from design import TOKENS, BASE

__all__ = ["WebUI"]

LOCAL_CSS = """
/* Web UI 本地组件与交互样式 */
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.app-shell{max-width:1120px;margin:0 auto;padding:var(--s4) var(--s4) var(--s7);min-height:100vh;display:flex;flex-direction:column}
.app-shell[hidden],.login-wrap[hidden],.tab-panel[hidden]{display:none!important}

/* 登录视图 */
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:var(--s4)}
.login-card{width:100%;max-width:380px;background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:var(--s6) var(--s5);box-shadow:var(--shadow)}
.login-card h1{text-align:center;margin-bottom:var(--s2)}
.login-card .subtitle{text-align:center;color:var(--muted);font-size:var(--fs-s);margin-bottom:var(--s5)}
.form-field{margin-bottom:var(--s4)}
.form-field label{display:block;font-size:var(--fs-s);font-weight:600;color:var(--ink);margin-bottom:var(--s1)}
.form-input{width:100%;padding:var(--s2) var(--s3);border:1px solid var(--line);border-radius:var(--r-s);background:var(--bg);color:var(--ink);font:400 var(--fs-m)/1.5 var(--font);transition:border-color .2s,box-shadow .2s}
.form-input:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
.form-input:disabled{opacity:.6;cursor:not-allowed}
.form-select{padding:var(--s2) var(--s3);border:1px solid var(--line);border-radius:var(--r-s);background:var(--surface);color:var(--ink);font:400 var(--fs-s)/1.5 var(--font);cursor:pointer}
.form-select:focus{outline:0;border-color:var(--accent)}
.form-error{padding:var(--s2) var(--s3);border-radius:var(--r-s);background:var(--bad-soft);color:var(--bad);font-size:var(--fs-s);margin-bottom:var(--s4)}

/* 按钮规范 */
.btn{display:inline-flex;align-items:center;justify-content:center;padding:var(--s2) var(--s4);border-radius:var(--r-s);font:600 var(--fs-s)/1.5 var(--font);cursor:pointer;border:1px solid transparent;transition:all .15s ease;text-decoration:none;white-space:nowrap}
.btn-primary{background:var(--accent);color:var(--on-accent);border-color:var(--accent)}
.btn-primary:hover{opacity:.9}
.btn-secondary{background:var(--sunk);color:var(--ink);border-color:var(--line)}
.btn-secondary:hover{background:var(--line)}
.btn-ghost{background:transparent;color:var(--muted);border-color:var(--line)}
.btn-ghost:hover{color:var(--ink);border-color:var(--ink)}
.btn-danger{background:var(--bad-soft);color:var(--bad);border-color:var(--bad-soft)}
.btn-danger:hover{border-color:var(--bad)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-sm{padding:var(--s1) var(--s2);font-size:var(--fs-s)}
.btn-block{width:100%;display:flex}

/* 顶部栏 */
.app-header{display:flex;align-items:center;justify-content:space-between;padding-bottom:var(--s4);margin-bottom:var(--s4);border-bottom:1px solid var(--line);flex-wrap:wrap;gap:var(--s3)}
.header-brand{display:flex;align-items:baseline;gap:var(--s3)}
.header-brand h1{font-size:var(--fs-xl);margin:0}
.header-account{font-size:var(--fs-s);color:var(--muted)}
.header-controls{display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap}

/* 导航 */
.desktop-nav{display:flex;gap:var(--s2);margin-bottom:var(--s5);border-bottom:1px solid var(--line);padding-bottom:0}
.tab-btn{background:none;border:none;padding:var(--s2) var(--s4);font:600 var(--fs-m)/1.5 var(--font);color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;border-radius:var(--r-s) var(--r-s) 0 0;transition:all .15s}
.tab-btn:hover{color:var(--ink)}
.tab-btn.is-active{color:var(--accent);border-bottom-color:var(--accent)}

.mobile-nav{display:none}
@media (max-width:640px){
.desktop-nav{display:none}
.mobile-nav{display:flex;position:fixed;bottom:0;left:0;right:0;height:56px;background:var(--surface);border-top:1px solid var(--line);z-index:90;box-shadow:var(--shadow)}
.mobile-nav .tab-btn{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:var(--s1) 0;font-size:var(--fs-s);border-bottom:none;border-top:2px solid transparent;border-radius:0}
.mobile-nav .tab-btn.is-active{border-top-color:var(--accent);color:var(--accent)}
.app-shell{padding-bottom:72px}
}

/* 反馈与通知 */
.banner{padding:var(--s3) var(--s4);border-radius:var(--r);margin-bottom:var(--s4);font-size:var(--fs-s)}
.banner.info{background:var(--accent-soft);color:var(--ink)}
.banner.warn{background:var(--warn-soft);color:var(--ink)}
.banner.error{background:var(--bad-soft);color:var(--bad)}
.banner[hidden]{display:none!important}
.toast{position:fixed;left:50%;bottom:var(--s5);z-index:100;display:flex;align-items:center;gap:var(--s3);max-width:calc(100% - 32px);padding:var(--s2) var(--s4);border-radius:999px;background:var(--ink);color:var(--bg);font-size:var(--fs-s);box-shadow:var(--shadow);transform:translateX(-50%)}
@media (max-width:640px){.toast{bottom:68px}}
.saving-indicator{display:inline-flex;align-items:center;gap:var(--s1);font-size:var(--fs-s);color:var(--muted)}
.ai-progress{width:min(480px,calc(100% - 32px));max-height:calc(100dvh - 48px);overflow:auto;padding:var(--s5);border:1px solid var(--line);border-radius:var(--r);background:var(--surface);color:var(--ink)}
@media (max-width:640px){.ai-progress{width:calc(100% - 24px);padding:var(--s4)}.ai-progress .btn{width:100%}}
.ai-progress::backdrop{background:rgb(0 0 0 / .45)}
.ai-progress h2{font-size:var(--fs-l);margin:0 0 var(--s3)}
.ai-progress p{overflow-wrap:anywhere;margin-bottom:var(--s3)}
.ai-progress .btn{margin-top:var(--s3)}
.ai-preview{margin-top:var(--s3);border:1px solid var(--line);border-radius:var(--r-s);background:var(--sunk);padding:var(--s3);display:flex;flex-direction:column;gap:var(--s2);box-sizing:border-box}
.ai-preview[hidden]{display:none!important}
.ai-preview-header{display:flex;align-items:center;justify-content:space-between;gap:var(--s2);flex-wrap:wrap}
.ai-preview-header .tag{margin-left:0}
.ai-preview-warning{font-size:var(--fs-s);color:var(--muted);line-height:1.5}
.ai-preview-body{max-height:220px;overflow-y:auto;overflow-x:hidden;font-size:var(--fs-s);line-height:1.7;color:var(--ink);white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;margin:0;padding-right:var(--s1);scrollbar-width:thin;scrollbar-color:var(--line) transparent}
.ai-preview-body::-webkit-scrollbar{width:6px}
.ai-preview-body::-webkit-scrollbar-thumb{background:var(--line);border-radius:999px}
.ai-preview-body:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:var(--r-s)}

/* 通用区块与容器 */
.section-gap{margin-top:var(--s4)}
.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--s3)}
.section-title{font-size:var(--fs-l);font-weight:600;margin:0}
.action-bar{display:flex;gap:var(--s2);align-items:center;flex-wrap:wrap;margin:var(--s3) 0}
.action-bar .form-input{flex:1 1 200px}
.filter-bar{display:flex;gap:var(--s2);align-items:center;flex-wrap:wrap;margin-bottom:var(--s4)}
.filter-group{display:inline-flex;border:1px solid var(--line);border-radius:var(--r-s);overflow:hidden;background:var(--sunk)}
.filter-btn{background:none;border:none;padding:var(--s1) var(--s3);font:500 var(--fs-s)/1.5 var(--font);color:var(--muted);cursor:pointer}
.filter-btn:hover{color:var(--ink)}
.filter-btn.is-active{background:var(--surface);color:var(--ink);font-weight:600}

/* 卡片与清单项 */
.item-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:var(--s3) var(--s4);margin-bottom:var(--s3);transition:border-color .15s}
.item-card:hover{border-color:var(--faint)}
.item-header{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--s3)}
.item-title{font-size:var(--fs-m);font-weight:600;line-height:1.4}
.item-header .code{margin-right:var(--s2)}
.item-meta{margin-top:var(--s1);display:flex;gap:var(--s2);align-items:center;flex-wrap:wrap;font-size:var(--fs-s);color:var(--muted)}
.item-actions{margin-top:var(--s3);display:flex;gap:var(--s2);align-items:center;flex-wrap:wrap}
.item-body{margin-top:var(--s2);padding-top:var(--s2);border-top:1px solid var(--line);font-size:var(--fs-s);line-height:1.6;white-space:pre-wrap}
.status-group{display:flex;gap:var(--s1);align-items:center;flex-wrap:wrap}
.rows .m{display:flex;gap:var(--s2);align-items:center;flex-wrap:wrap;justify-content:flex-end}
.item-title,.item-body,.report-content,.ai-announcement-callout{overflow-wrap:anywhere}
.task-countdown{display:inline-flex;align-items:center;padding:1px var(--s2);border-radius:var(--r-s);font-size:var(--fs-s);font-weight:600;line-height:1.5;white-space:nowrap}
.task-countdown.is-overdue,.task-countdown.is-urgent{background:var(--bad-soft);color:var(--bad)}
.task-countdown.is-warn{background:var(--warn-soft);color:var(--warn)}
.task-countdown.is-normal{background:var(--sunk);color:var(--muted)}
@media(max-width:640px){.rows .m{justify-content:flex-start}.settings-card{padding:var(--s4)}.settings-card:first-child .setting-row{align-items:stretch;flex-direction:column}}
.range-card{padding:var(--s2) var(--s3);background:var(--sunk);border-radius:var(--r-s);margin-bottom:var(--s3);font-size:var(--fs-s);color:var(--muted)}
.ai-announcement-callout{margin-top:var(--s2);padding:var(--s2) var(--s3);background:var(--accent-soft);border-radius:var(--r-s);font-size:var(--fs-s)}
.ai-announcement-callout b{display:block;margin-bottom:var(--s1);color:var(--accent)}
.study-layout{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--s6);margin-top:var(--s4)}
.study-layout>*{min-width:0}
.study-primary{max-width:72ch}
.study-layout .section-title{font-size:var(--fs-l);line-height:1.4}
.study-primary>.item-meta{margin:var(--s2) 0 0;line-height:1.6}
.study-primary .study-item{border:0;padding:var(--s4) 0 var(--s2)}
.study-primary .study-item-heading{display:flex;flex-direction:column;align-items:flex-start;gap:var(--s2);font-size:var(--fs-xl);line-height:1.4}
.study-primary .study-item-heading>a{font-weight:700;color:var(--ink);text-decoration:none;text-wrap:pretty}
.study-primary .study-item-heading>a:hover{text-decoration:underline;text-underline-offset:4px}
.study-primary .study-first-step{font-size:var(--fs-m);margin:var(--s4) 0 var(--s2)}
.study-primary .study-item .item-actions .btn{background:var(--accent);color:var(--on-accent);border-color:var(--accent);padding:var(--s2) var(--s4)}
.study-primary .btn,.announcement-actions .btn{min-height:44px}
.study-week-list summary{min-height:44px;padding:var(--s2) 0}
.study-week-list{counter-reset:priority;margin:var(--s3) 0 0;padding:0;list-style:none}
.study-week-list>li{counter-increment:priority;display:grid;grid-template-columns:24px minmax(0,1fr);gap:var(--s2);padding:var(--s3) 0;border-top:1px solid var(--line);overflow-wrap:anywhere}
.study-week-list>li:before{content:counter(priority,decimal-leading-zero);color:var(--muted);font-size:var(--fs-s);font-variant-numeric:tabular-nums;line-height:1.7}
.study-week-list a{font-weight:600;color:var(--ink);line-height:1.6;text-decoration:none}
.study-week-list a:hover{text-decoration:underline;text-underline-offset:4px}
.study-week-list .item-meta{margin:var(--s1) 0 0;line-height:1.6}
.study-week-list .study-edit{margin-top:var(--s1)}
.study-secondary{display:grid;grid-template-columns:minmax(0,1fr);margin-top:var(--s5);padding-top:var(--s2);border-top:1px solid var(--line)}
.study-secondary>details.fold{margin:0;border-bottom:1px solid var(--line);min-width:0}
.study-secondary>details.fold>summary{min-height:44px;display:flex;align-items:center;font-size:var(--fs-s);line-height:1.6;padding:var(--s2) 0}
.study-secondary>.btn{justify-self:start}
.study-secondary>.callout{margin:var(--s3) 0;padding:var(--s4);line-height:1.7}
.study-secondary>.callout p{margin:var(--s2) 0 0;max-width:90ch}
.study-item{padding:var(--s3) 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
.study-item:last-child{border-bottom:0}
.study-item-heading{display:flex;align-items:baseline;flex-wrap:wrap;gap:var(--s2);font-weight:600}
.study-first-step{margin:var(--s2) 0;font-size:var(--fs-s);line-height:1.7;max-width:72ch}
.study-edit{margin-top:var(--s3);font-size:var(--fs-s)}
.study-edit summary{cursor:pointer;color:var(--accent)}
.study-edit-form{display:grid;gap:var(--s3);margin-top:var(--s3);max-width:560px}
.study-edit-form label{display:grid;gap:var(--s1)}
.study-edit-form textarea{min-height:80px;resize:vertical}
.study-edit-form .btn{justify-self:start;white-space:normal}
.study-day{padding:var(--s4) 0;border-top:1px solid var(--line)}
.study-day h4{font-size:var(--fs-m);margin:0 0 var(--s2);font-variant-numeric:tabular-nums}
.study-evidence{margin:var(--s3) 0;padding:var(--s3);background:var(--sunk);border:0;color:var(--ink);font-size:var(--fs-s);line-height:1.6}
.announcement-actions h4{font-size:var(--fs-m);margin:0 0 var(--s2)}
.study-plan .callout{overflow-wrap:anywhere}
.today-status{display:flex;align-items:baseline;gap:var(--s2) var(--s3);flex-wrap:wrap;padding:var(--s2) 0;font-size:var(--fs-s);color:var(--muted)}
.today-status b{color:var(--ink)}
.today-status.is-warn b{color:var(--warn)}
.today-status.is-bad b{color:var(--bad)}
.today-status .why{margin:0}
.today-tools{display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap;margin:var(--s4) 0}
.today-tools .form-input{flex:1 1 220px}
.today-tools .quota-note{flex-basis:100%;font-size:var(--fs-s);color:var(--muted)}
.report-group{border-top:1px solid var(--line);padding:var(--s2) 0}
.report-group summary{cursor:pointer;font-weight:600;min-height:44px;padding:var(--s2) 0}
.report-group .report-attention{color:var(--warn)}
.report-group .report-entry{padding:var(--s2) 0;white-space:pre-wrap;overflow-wrap:anywhere;border-top:1px solid var(--line)}
.report-group .report-entry:first-of-type{border-top:0}
.report-notes{margin:var(--s2) 0 var(--s3);display:grid;gap:var(--s1);font-size:var(--fs-s)}
.report-notes .is-alert{color:var(--bad);font-weight:600}
.ai-archive{padding:var(--s2) 0;border-top:1px solid var(--line)}
.ai-archive summary{cursor:pointer;font-weight:600;overflow-wrap:anywhere}
.ai-archive[open] .ai-announcement-callout{margin-top:var(--s2)}
@media(max-width:760px){.study-layout{gap:var(--s5)}.study-secondary{margin-top:var(--s4)}}
@media(max-width:640px){.today-tools .form-input{flex-basis:100%}.today-tools .quota-note{order:-1}.item-actions .btn{white-space:normal}}
@media(max-width:640px){#tab-today .rows.nobox>li{grid-template-columns:minmax(0,1fr)}#tab-today .rows .m{white-space:normal;text-align:left}#tab-today .item-actions{align-items:stretch}.study-week-list>li{grid-template-columns:20px minmax(0,1fr)}}
/* 学习日历 */
.cal-header{display:flex;align-items:center;justify-content:space-between;gap:var(--s2);flex-wrap:wrap}
.cal-nav{display:flex;align-items:center;gap:var(--s2)}
.cal-title{font-weight:600;min-width:7em;text-align:center}
.cal-legend{display:flex;flex-wrap:wrap;gap:var(--s3);margin:var(--s2) 0;font-size:var(--fs-s);color:var(--muted)}
.cal-legend-item{display:inline-flex;align-items:center;gap:var(--s1)}
.cal-mark{display:inline-block;line-height:1;font-size:12px}
.cal-assignment{color:var(--accent)}.cal-exam{color:var(--bad)}.cal-announcement{color:var(--warn)}.cal-other{color:var(--good)}
.cal-mark.is-candidate{color:transparent;-webkit-text-stroke:1px currentColor}
.cal-assignment.is-candidate{-webkit-text-stroke-color:var(--accent)}.cal-exam.is-candidate{-webkit-text-stroke-color:var(--bad)}
.cal-announcement.is-candidate{-webkit-text-stroke-color:var(--warn)}.cal-other.is-candidate{-webkit-text-stroke-color:var(--good)}
.cal-mark.is-done{opacity:.4}
.cal-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:2px}
.cal-weekday{text-align:center;font-size:var(--fs-s);color:var(--muted);padding:var(--s1) 0}
.cal-day{position:relative;min-height:84px;padding:var(--s1);border:1px solid var(--line);border-radius:var(--r-s);background:var(--surface);color:var(--ink);font:inherit;text-align:left;display:flex;flex-direction:column;gap:2px;cursor:pointer;overflow:hidden}
.cal-day.is-empty{border:0;background:transparent;cursor:default}
.cal-day:hover{border-color:var(--accent)}
.cal-day:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.cal-day.is-today{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.cal-day.is-today .cal-num{color:var(--accent);font-weight:700}
.cal-num{font-size:var(--fs-s)}
.cal-alert{position:absolute;top:2px;right:4px;color:var(--bad);font-weight:700}
.cal-bars{display:flex;flex-direction:column;gap:1px;min-width:0}
.cal-bar{display:flex;align-items:center;gap:3px;font-size:11px;min-width:0}
.cal-bar-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cal-more{font-size:11px;color:var(--muted)}
.cal-dots{display:none;gap:2px;flex-wrap:wrap}
.cal-summary{font-size:var(--fs-s);color:var(--muted);margin-top:var(--s2)}
.cal-undated,.cal-syllabus{border-top:1px solid var(--line);padding:var(--s2) 0;margin-top:var(--s2)}
.cal-undated summary,.cal-syllabus summary{cursor:pointer;font-weight:600;min-height:44px;display:flex;align-items:center}
.cal-syllabus-form{display:flex;flex-wrap:wrap;gap:var(--s2);align-items:center;margin:var(--s2) 0}
.cal-select,.cal-link-input,.cal-date-input{padding:var(--s1) var(--s2);border:1px solid var(--line);border-radius:var(--r-s);background:var(--bg);color:var(--ink);font:inherit}
.cal-link-input{flex:1 1 220px}
.cal-source{display:flex;justify-content:space-between;align-items:center;gap:var(--s2);padding:var(--s1) 0;font-size:var(--fs-s)}
.cal-dialog{width:min(520px,calc(100% - 32px));max-height:calc(100dvh - 48px);overflow:auto;padding:var(--s5);border:1px solid var(--line);border-radius:var(--r);background:var(--surface);color:var(--ink)}
.cal-dialog::backdrop{background:rgb(0 0 0 / .45)}
.cal-dialog-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--s3)}
.cal-event{border-top:1px solid var(--line);padding:var(--s3) 0}
.cal-event.is-candidate{border-left:3px dashed var(--faint);padding-left:var(--s2)}
.cal-event.is-done .cal-event-title{text-decoration:line-through;color:var(--muted)}
.cal-event-head{display:flex;align-items:center;gap:var(--s2)}
.cal-event-meta{font-size:var(--fs-s);color:var(--muted);margin:var(--s1) 0;overflow-wrap:anywhere}
.cal-evidence{margin:var(--s1) 0;padding:var(--s1) var(--s2);border-left:3px solid var(--line);background:var(--sunk);font-size:var(--fs-s);overflow-wrap:anywhere}
.cal-conflict{margin:var(--s2) 0;padding:var(--s2);border-radius:var(--r-s);background:var(--warn-soft);color:var(--ink);font-size:var(--fs-s)}
.cal-event-actions{display:flex;flex-wrap:wrap;gap:var(--s2);margin-top:var(--s2);align-items:center}
@media(max-width:640px){.cal-day{min-height:48px;align-items:center}.cal-bars{display:none}.cal-dots{display:flex;justify-content:center}.cal-dialog{width:calc(100% - 24px);padding:var(--s4)}.cal-syllabus-form>*{flex-basis:100%}}

/* 设置区块 */
.settings-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:var(--s4) var(--s5);margin-bottom:var(--s4)}
.settings-card h3{margin-bottom:var(--s3);font-size:var(--fs-l)}
.setting-row{display:flex;align-items:center;justify-content:space-between;gap:var(--s4);padding:var(--s3) 0;border-top:1px solid var(--line)}
.setting-row:first-of-type{border-top:none}
.setting-info{flex:1}
.setting-title{font-size:var(--fs-m);font-weight:600}
.setting-desc{font-size:var(--fs-s);color:var(--muted);margin-top:2px}
.course-list-item{display:flex;align-items:center;justify-content:space-between;padding:var(--s2) 0;border-top:1px solid var(--line);gap:var(--s2)}
.course-list-item:first-child{border-top:none}
.grid-2col{display:grid;grid-template-columns:1fr 1fr;gap:var(--s3)}
@media (max-width:640px){.grid-2col{grid-template-columns:1fr}}
.empty-hint{padding:var(--s5) 0;text-align:center;color:var(--muted);font-size:var(--fs-s)}
.report-content{font-size:var(--fs-s);line-height:1.7;color:var(--ink);white-space:pre-wrap;margin-top:var(--s2)}
"""


class WebUI:
    """救驾 Web 交互前端静态资产提供类"""

    @staticmethod
    def stylesheet() -> str:
        """组合设计系统 Tokens、Base 重置及 Web 交互端局部 CSS"""
        return f"{TOKENS}\n{BASE}\n{LOCAL_CSS}"

    @staticmethod
    def page() -> str:
        """返回完整 SPA 单页 HTML，遵循 CSP 规范，无内联脚本与样式"""
        return (
            "<!DOCTYPE html>\n"
            '<html lang="zh-CN">\n'
            "<head>\n"
            '  <meta charset="utf-8">\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
            "  <title>救驾</title>\n"
            '  <link rel="stylesheet" href="/assets/style.css">\n'
            '  <script src="/assets/app.js" defer></script>\n'
            "</head>\n"
            "<body>\n"
            '<div id="aria-status" class="sr-only" aria-live="polite" aria-atomic="true"></div>\n\n'
            '<!-- 登录视图 -->\n'
            '<div id="view-login" class="login-wrap">\n'
            '  <div class="login-card">\n'
            "    <h1>救驾</h1>\n"
            '    <p class="subtitle">Canvas 学习手帐与提醒</p>\n'
            '    <div id="login-error" class="form-error" role="alert" aria-live="polite" hidden></div>\n'
            '    <form id="login-form">\n'
            '      <div class="form-field">\n'
            '        <label for="login-username">用户名</label>\n'
            '        <input id="login-username" name="username" type="text" class="form-input" autocomplete="username" required>\n'
            "      </div>\n"
            '      <div class="form-field">\n'
            '        <label for="login-password">密码</label>\n'
            '        <input id="login-password" name="password" type="password" class="form-input" autocomplete="current-password" required>\n'
            "      </div>\n"
            '      <button id="login-submit" type="submit" class="btn btn-primary btn-block">登录</button>\n'
            "    </form>\n"
            "  </div>\n"
            "</div>\n\n"
            '<!-- 认证后主视图 -->\n'
            '<div id="view-app" class="app-shell" hidden>\n'
            '  <header class="app-header">\n'
            '    <div class="header-brand">\n'
            "      <h1>救驾</h1>\n"
            '      <span id="header-account" class="header-account"></span>\n'
            "    </div>\n"
            '    <div class="header-controls">\n'
            '      <span id="header-timestamp" class="meta num"></span>\n'
            '      <span id="header-saving" class="saving-indicator" hidden>保存中...</span>\n'
            '      <button id="btn-theme" type="button" class="btn btn-ghost btn-sm" aria-label="切换浅色/深色主题">深色</button>\n'
            '      <button id="btn-logout" type="button" class="btn btn-ghost btn-sm">退出</button>\n'
            "    </div>\n"
            "  </header>\n\n"
            '  <nav class="desktop-nav" aria-label="主要导航">\n'
            '    <button type="button" class="tab-btn is-active" data-tab="today" role="tab" aria-selected="true" aria-controls="tab-today" id="tab-btn-today">今日</button>\n'
            '    <button type="button" class="tab-btn" data-tab="tasks" role="tab" aria-selected="false" aria-controls="tab-tasks" id="tab-btn-tasks">作业</button>\n'
            '    <button type="button" class="tab-btn" data-tab="announcements" role="tab" aria-selected="false" aria-controls="tab-announcements" id="tab-btn-announcements">公告</button>\n'
            '    <button type="button" class="tab-btn" data-tab="settings" role="tab" aria-selected="false" aria-controls="tab-settings" id="tab-btn-settings">设置</button>\n'
            "  </nav>\n\n"
            '  <div id="banner-region" class="banner" role="region" aria-live="polite" hidden></div>\n\n'
            '  <main class="app-main">\n'
            '    <!-- 今日 Tab -->\n'
            '    <section id="tab-today" class="tab-panel" role="tabpanel" aria-labelledby="tab-btn-today">\n'
            "    </section>\n\n"
            '    <!-- 作业 Tab -->\n'
            '    <section id="tab-tasks" class="tab-panel" role="tabpanel" aria-labelledby="tab-btn-tasks" hidden>\n'
            "    </section>\n\n"
            '    <!-- 公告 Tab -->\n'
            '    <section id="tab-announcements" class="tab-panel" role="tabpanel" aria-labelledby="tab-btn-announcements" hidden>\n'
            '    </section>\n\n'
            '    <!-- 设置 Tab -->\n'
            '    <section id="tab-settings" class="tab-panel" role="tabpanel" aria-labelledby="tab-btn-settings" hidden>\n'
            "    </section>\n"
            "  </main>\n\n"
            '  <nav class="mobile-nav" aria-label="移动端导航">\n'
            '    <button type="button" class="tab-btn is-active" data-tab="today" role="tab" aria-selected="true" aria-controls="tab-today">今日</button>\n'
            '    <button type="button" class="tab-btn" data-tab="tasks" role="tab" aria-selected="false" aria-controls="tab-tasks">作业</button>\n'
            '    <button type="button" class="tab-btn" data-tab="announcements" role="tab" aria-selected="false" aria-controls="tab-announcements">公告</button>\n'
            '    <button type="button" class="tab-btn" data-tab="settings" role="tab" aria-selected="false" aria-controls="tab-settings">设置</button>\n'
            "  </nav>\n\n"
            '  <div id="app-toast" class="toast" hidden><span id="toast-text"></span></div>\n'
            "</div>\n\n"
            "</body>\n"
            "</html>"
        )
