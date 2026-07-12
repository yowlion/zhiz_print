// 智兆模板平台 - 独立 Page(浏览/预览/评论/安装平台打印模板)
// 数据走 zhiz_print.api.template_store.*(list/get/comment/install),后者再调 zhiz_licser template_api

frappe.pages['print-template-store'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({ parent: wrapper, title: __('模板平台'), single_column: false });
    $(wrapper).find('.page-form.row.hide').remove();
    // 面包屑:v15 用 Frappe breadcrumbs API(patch set_custom_breadcrumbs 支持 items 多级);
    // v16 原生 set_custom_breadcrumbs 只认单 route+label,Custom 渲染空链接,改手动 DOM 渲染完整面包屑
    const _fv = (frappe.boot.versions && frappe.boot.versions.frappe) || '';
    const _isV16 = _fv.startsWith('16');
    if (!_isV16) {
        if (!frappe.breadcrumbs._pts_patched) {
            const _orig_scb = frappe.breadcrumbs.set_custom_breadcrumbs;
            frappe.breadcrumbs.set_custom_breadcrumbs = function (bc) {
                if (bc && bc.items) {
                    this.$breadcrumbs.empty();
                    bc.items.forEach(it => this.append_breadcrumb_element(it.route, it.label));
                } else {
                    _orig_scb.call(this, bc);
                }
            };
            frappe.breadcrumbs._pts_patched = true;
        }
        frappe.breadcrumbs.all['print-template-store'] = {
            type: 'Custom',
            items: [
                { route: '/app/super-print-design', label: __('高级打印设计') },
                { route: '/app/print-template-store', label: __('模板平台') },
            ],
        };
        frappe.breadcrumbs.update();
    } else {
        // v16: 不设 Custom all(原生 set_custom_breadcrumbs 只单级,Custom 渲染空链接);
        // 用 frappe 自带 append_breadcrumb_element 在 update 后(只剩 home)补 workspace + 当前页
        const renderBC = () => {
            const $bc = $(wrapper).find('.navbar-breadcrumbs');
            if (!$bc.length) return;
            // 删 v16 update 渲染的空链接 li(href 相对路径/无文本,如 print-template-store 空链接)
            $bc.find('li').each(function () {
                const $a = $(this).children('a');
                if ($a.length && (!$a.attr('href') || !$a.attr('href').startsWith('/')) && !$a.text().trim()) {
                    $(this).remove();
                }
            });
            if ($bc.find('a[href$="/desk/print-template-store"]').length) return;  // 已补 workspace+当前页
            frappe.breadcrumbs.$breadcrumbs = $bc;
            frappe.breadcrumbs.append_breadcrumb_element('/desk/super-print-design', __('高级打印设计'), 'worksapce-breadcrumb');
            frappe.breadcrumbs.append_breadcrumb_element('/desk/print-template-store', __('模板平台'), 'title-text');
        };
        [0, 100, 300, 800, 1500].forEach(ms => setTimeout(renderBC, ms));
        const observeBC = () => {
            const $bc0 = $(wrapper).find('.navbar-breadcrumbs');
            if ($bc0.length) new MutationObserver(renderBC).observe($bc0[0], { childList: true });
            else setTimeout(observeBC, 100);
        };
        observeBC();
    }

    // 顶部刷新按钮
    const $iconGroup = $(wrapper).find('.page-icon-group');
    $iconGroup.removeClass('hide hidden-xs hidden-sm')
        .html(`<button class="text-muted btn btn-default icon-btn" id="pts-reload-btn" title="${__('刷新')}">${frappe.utils.icon('es-line-reload', 'icon-sm')}</button>`);

    const $sidebar = $(wrapper).find('.layout-side-section');
    const $main = $(wrapper).find('.layout-main-section');
    // EN16 layout: .layout-main(flex) > .layout-main-section-wrapper(80%) + .layout-side-section(20%)
    // 用 CSS order 把边栏移右、收窄,不动 DOM(动 DOM 会把 side 塞进 wrapper 致堆叠)
    $(wrapper).addClass('pts-page');

    // CSS
    $('head').append(`
    <style id="pts-layout">
        .pts-page .layout-main { display:flex !important; }
        .zhiz-v16 .pts-page .layout-main-section-wrapper { flex:1 1 auto !important; width:auto !important; max-width:none !important; order:1 !important; }
        .zhiz-v16 .pts-page .layout-side-section { flex:0 0 13% !important; width:13% !important; max-width:13% !important; min-width:0 !important; order:2 !important; }
        .pts-sidebar .pts-cat { padding:7px 12px; margin-bottom:3px; border-radius:7px; cursor:pointer; font-size:12px; color:#6e6e73; }
        .pts-sidebar .pts-cat:hover { background:#f5f5f7; }
        .pts-sidebar .pts-cat.active { background:var(--zhiz-super-accent,#007AFF); color:#fff; }
        .pts-sidebar .pts-cat .pts-cat-count { float:right; opacity:.65; }
        .pts-main { padding:0 8px 16px; }
        .pts-search { margin-bottom:10px; }
        .pts-grid { display:grid; grid-template-columns:repeat(6, 1fr); gap:12px; }
        @media(max-width:1700px){ .pts-grid{ grid-template-columns:repeat(5,1fr);} }
        @media(max-width:1450px){ .pts-grid{ grid-template-columns:repeat(4,1fr);} }
        @media(max-width:1150px){ .pts-grid{ grid-template-columns:repeat(3,1fr);} }
        .pts-card { background:#fff; border:1px solid #e8eaed; border-radius:10px; box-shadow:0 1px 3px rgba(0,0,0,0.04); cursor:pointer; transition:transform .12s, box-shadow .12s; overflow:hidden; }
        .pts-card:hover { transform:translateY(-2px); box-shadow:0 4px 12px rgba(0,0,0,0.1); border-color:#d1d1d6; }
        .pts-card-thumb { height:105px; background:#f0f0f2; overflow:hidden; position:relative; }
        .pts-card-thumb iframe { position:absolute; top:0; left:50%; border:0; transform-origin:top center; pointer-events:none; }
        .pts-card-body { padding:7px 9px; font-size:11px; }
        .pts-card-name { font-weight:600; color:#1d1d1f; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .pts-card-meta { color:#86868b; margin-top:2px; }
        .pts-card-co { color:var(--zhiz-super-accent,#007AFF); margin-top:2px; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .pts-empty { padding:40px; text-align:center; color:#aeaeb2; font-size:13px; }
        .pts-preview-tabs { display:flex; gap:4px; margin-bottom:6px; }
        .pts-preview-tabs .pts-tab { padding:5px 16px; border-radius:6px 6px 0 0; font-size:12px; cursor:pointer; background:#e0e0e0; color:#666; }
        .pts-preview-tabs .pts-tab.active { background:var(--zhiz-super-accent,#007AFF); color:#fff; }
        .pts-dlg-preview { width:100%; min-height:420px; border:1px solid #e8eaed; border-radius:8px; background:#f0f0f2; }
        .pts-paper-info { background:#f5f5f7; border-radius:8px; padding:8px 12px; font-size:12px; color:#6e6e73; margin:8px 0; }
        .pts-paper-info b { color:#1d1d1f; }
        .pts-comments { height:200px; overflow-y:auto; margin:6px 0; }
        .pts-comment { padding:6px 0; border-bottom:1px solid #f0f0f2; font-size:12px; }
        .pts-comment-author { font-weight:600; color:var(--zhiz-super-accent,#007AFF); }
        .pts-comment-date { color:#aeaeb2; font-size:10px; float:right; }
        .pts-comment-text { color:#1d1d1f; margin-top:2px; white-space:pre-wrap; word-break:break-word; }
    </style>`);

    $sidebar.html(`<div class="pts-sidebar">
        <div class="pts-cat active" data-cat="all">${__('全部')} <span class="pts-cat-count" id="pts-cat-all-count">0</span></div>
        <div id="pts-cat-list"></div>
    </div>`);
    $main.html(`<div class="pts-main">
        <div class="pts-search"><input type="text" id="pts-search" class="form-control input-sm" placeholder="${__('搜索模板名...')}"></div>
        <div id="pts-grid" class="pts-grid"><div class="pts-empty">${__('加载中...')}</div></div>
    </div>`);

    let allTemplates = [];
    let activeCategory = 'all';
    let keyword = '';

    $('#pts-reload-btn').on('click', () => { try { localStorage.removeItem(PTS_LIST_CACHE); } catch (e) {} loadTemplates(true); });
    $('#pts-search').on('input', function () { keyword = $(this).val().toLowerCase().trim(); renderCards(); });
    $sidebar.on('click', '.pts-cat', function () {
        $sidebar.find('.pts-cat').removeClass('active');
        $(this).addClass('active');
        activeCategory = $(this).data('cat');
        renderCards();
    });

    const PTS_LIST_CACHE = 'pts_list_cache';
    const PTS_LIST_TTL = 10 * 60 * 1000;  // 10分钟
    loadTemplates();

    function loadTemplates(force) {
        // 方案2:先用本地缓存渲染(秒开),后台静默刷新
        if (!force) {
            try {
                const cached = JSON.parse(localStorage.getItem(PTS_LIST_CACHE) || 'null');
                if (cached && cached.ts && (Date.now() - cached.ts < PTS_LIST_TTL) && cached.templates) {
                    allTemplates = cached.templates;
                    renderCategories();
                    renderCards();
                    _fetchTemplates();  // 后台静默刷新
                    return;
                }
            } catch (e) {}
        }
        $('#pts-grid').html(`<div class="pts-empty">${__('加载中...')}</div>`);
        _fetchTemplates();
    }

    function _fetchTemplates() {
        frappe.call({
            method: 'zhiz_print.api.template_store.list_templates',
            args: {},
            callback: (r) => {
                const res = r.message || {};
                allTemplates = res.templates || [];
                try { localStorage.setItem(PTS_LIST_CACHE, JSON.stringify({ ts: Date.now(), templates: allTemplates })); } catch (e) {}
                renderCategories();
                renderCards();
            }
        });
    }

    function renderCategories() {
        const counts = {};
        allTemplates.forEach(t => { const k = t.target_doctype || __('其他'); counts[k] = (counts[k] || 0) + 1; });
        $('#pts-cat-all-count').text(allTemplates.length);
        const cats = Object.keys(counts).sort();
        $('#pts-cat-list').html(cats.map(c =>
            `<div class="pts-cat" data-cat="${frappe.utils.escape_html(c)}">${__(c)} <span class="pts-cat-count">${counts[c]}</span></div>`
        ).join(''));
    }

    function renderCards() {
        let list = allTemplates;
        if (activeCategory !== 'all') list = list.filter(t => (t.target_doctype || __('其他')) === activeCategory);
        if (keyword) list = list.filter(t => (t.template_name || '').toLowerCase().includes(keyword));

        if (!list.length) { $('#pts-grid').html(`<div class="pts-empty">${__('暂无模板')}</div>`); return; }

        $('#pts-grid').html(list.map(t => `
            <div class="pts-card" data-name="${frappe.utils.escape_html(t.name)}">
                <div class="pts-card-thumb"><iframe></iframe></div>
                <div class="pts-card-body">
                    <div class="pts-card-name">${frappe.utils.escape_html(t.template_name || '')}</div>
                    <div class="pts-card-meta">${__(t.target_doctype || '')} · v${t.version || 1} · ↓${t.download_count || 0}</div>
                    <div class="pts-card-co">广德智兆科技有限公司</div>
                </div>
            </div>`).join(''));

        // 方案3:卡片缩略懒加载——IntersectionObserver 可见时才拉 preview + 本地缓存
        const _writeThumb = (ifr, html) => {
            try {
                const d = ifr.contentWindow.document;
                d.open(); d.write(html); d.close();
                const st = d.createElement('style');
                st.textContent = 'body{background:#f0f0f0 !important;margin:0 !important;padding:20px !important;} .print-pages-wrapper{margin:0 auto !important;} .print-page{background:#fff !important;box-shadow:0 2px 16px rgba(0,0,0,.12) !important;margin:0 0 20px 0 !important;}';
                (d.head || d.documentElement).appendChild(st);
                const firstPage = d.querySelector('.print-page');
                const pw = (firstPage && firstPage.offsetWidth) ? firstPage.offsetWidth : 794;
                const iframeW = pw + 40;
                ifr.style.width = iframeW + 'px';
                ifr.style.height = '1123px';
                const thumbW = ifr.parentElement.offsetWidth || 167;
                ifr.style.transform = 'translateX(-50%) scale(' + Math.min(0.21, thumbW / iframeW).toFixed(4) + ')';
            } catch (e) {}
        };
        const _loadCardThumb = (card) => {
            const name = card.dataset.name;
            const ck = 'pts_thumb_' + name;
            let html = null;
            try { html = localStorage.getItem(ck); } catch (e) {}
            if (html) {
                const ifr = card.querySelector('.pts-card-thumb iframe');
                if (ifr) { _writeThumb(ifr, html); return; }
            }
            // 本地无缓存:显示 spinner,异步 get_template 拉(返回已脱敏 preview_html)
            card.querySelector('.pts-card-thumb').innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#999;font-size:11px;"><i class="fa fa-spinner fa-spin"></i></div>';
            frappe.call({
                method: 'zhiz_print.api.template_store.get_template',
                args: { template_id: name },
                callback: (r) => {
                    const tpl = r.message;
                    if (!tpl || tpl.error) {
                        // 超时/失败:静默(不弹窗),占位改文件图标;详情仍可点开重试
                        card.querySelector('.pts-card-thumb').innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#bbb;font-size:18px;"><i class="fa fa-file-text-o"></i></div>';
                        return;
                    }
                    card.querySelector('.pts-card-thumb').innerHTML = '<iframe></iframe>';
                    const ifr2 = card.querySelector('.pts-card-thumb iframe');
                    if (tpl.preview_html && ifr2) {
                        try { localStorage.setItem(ck, tpl.preview_html); } catch (e) {}
                        _writeThumb(ifr2, tpl.preview_html);
                    }
                }
            });
        };
        const _obs = new IntersectionObserver((entries) => {
            entries.forEach(en => {
                if (en.isIntersecting) { _loadCardThumb(en.target); _obs.unobserve(en.target); }
            });
        }, { rootMargin: '150px' });
        document.querySelectorAll('#pts-grid .pts-card').forEach(c => _obs.observe(c));

        $('#pts-grid').off('click', '.pts-card').on('click', '.pts-card', function () {
            const name = $(this).data('name');
            const meta = allTemplates.find(t => t.name === name) || { name: name };
            // 列表精简后 allTemplates 无 preview_html,click 时 get_template 拉完整(含 preview_html/preview_html_design)
            frappe.dom.freeze(__('加载中...'));
            frappe.call({
                method: 'zhiz_print.api.template_store.get_template',
                args: { template_id: name },
                callback: (r) => {
                    frappe.dom.unfreeze();
                    const full = r.message;
                    openPreview(full && !full.error ? Object.assign({}, meta, full) : meta);
                },
                error: () => { frappe.dom.unfreeze(); openPreview(meta); }
            });
        });
    }

    function openPreview(tpl) {
        const dlg = new frappe.ui.Dialog({
            title: __(tpl.template_name),
            size: 'extra-large',
            fields: [
                { fieldtype: 'HTML', fieldname: 'preview',
                  options: `<div class="pts-preview-tabs">
                      <span class="pts-tab active" data-tab="actual">${__('打印效果')}</span>
                      <span class="pts-tab" data-tab="design">${__('设计效果')}</span>
                  </div>
                  <div style="height:40vh;overflow:auto;background:#f0f0f0;border-radius:8px;"><iframe id="pts-dlg-iframe" class="pts-dlg-preview"></iframe></div>` },
                { fieldtype: 'HTML', fieldname: 'paper',
                  options: `<div class="pts-paper-info" id="pts-paper-info">${__('加载中...')}</div>` },
                { fieldtype: 'HTML', fieldname: 'actions_comments',
                  options: `<div style="display:flex;align-items:center;gap:12px;margin:4px 0;">
                              <button class="btn btn-success btn-sm" id="pts-install-btn"><i class="fa fa-download"></i> ${__('下载安装')}</button>
                              <span style="color:#86868b;font-size:12px;">${__('下载次数')}: <b style="color:#1d1d1f;">${tpl.download_count || 0}</b></span>
                            </div>
                            <hr style="margin:8px 0;">
                            <div style="font-weight:600;">${__('评论')}</div>
                            <div style="margin-top:6px;margin-bottom:6px;">
                                <textarea id="pts-new-comment" class="form-control input-sm" placeholder="${__('留言...')} (Ctrl+Enter ${__('提交')})" style="resize:vertical;height:60px;min-height:60px;"></textarea>
                                <div style="text-align:right;margin-top:4px;"><button class="btn btn-sm btn-default" id="pts-submit-comment">${__('提交')}</button></div>
                            </div>
                            <div class="pts-comments" id="pts-comments">${__('加载中...')}</div>` },
            ],
        });
        // 写完整预览 + 注入纸张 CSS。时机:shown.bs.modal(modal 显示动画 ~300ms 完成后 iframe contentDocument 才稳定可写) + setTimeout 兜底 + iframe 未渲染重试
        const writePreview = (html) => {
            const ifr = dlg.$wrapper.find('#pts-dlg-iframe')[0];
            if (!ifr) { setTimeout(() => writePreview(html), 50); return; }
            try {
                const d = ifr.contentWindow.document;
                d.open(); d.write(html || ''); d.close();
                const st = d.createElement('style');
                st.textContent = 'body{background:#f0f0f0 !important;margin:0 !important;padding:20px !important;} .print-pages-wrapper{margin:0 auto !important;} .print-page{background:#fff !important;box-shadow:0 2px 16px rgba(0,0,0,.12) !important;margin:0 0 20px 0 !important;}';
                (d.head || d.documentElement).appendChild(st);
            } catch (e) {}
            const resize = () => { try { const cd = ifr.contentWindow.document; const h = Math.max(cd.body.scrollHeight, cd.documentElement.scrollHeight, cd.body.offsetHeight); if (h > 0) ifr.style.height = (h + 16) + 'px'; } catch (e2) {} };
            [100, 500, 1500].forEach(ms => setTimeout(resize, ms));
        };
        dlg.show();
        dlg.$wrapper.on('shown.bs.modal', () => writePreview(tpl.preview_html || ''));
        setTimeout(() => writePreview(tpl.preview_html || ''), 100);
        setTimeout(() => writePreview(tpl.preview_html || ''), 600);
        // 页签切换:实际打印(preview_html)/设计渲染(preview_html_design 占位符)
        dlg.$wrapper.on('click', '.pts-tab', (e) => {
            const tab = $(e.currentTarget).data('tab');
            dlg.$wrapper.find('.pts-tab').removeClass('active');
            $(e.currentTarget).addClass('active');
            writePreview(tab === 'design' ? (tpl.preview_html_design || '') : (tpl.preview_html || ''));
        });

        loadPaperInfo(tpl, dlg.$wrapper);
        loadComments(tpl.name, dlg.$wrapper);

        // 事件绑定限定本 dialog(事件委托,避免全局 id 冲突)
        dlg.$wrapper.on('click', '#pts-install-btn', () => openInstallDialog(tpl));
        const submitComment = () => {
            const $ta = dlg.$wrapper.find('#pts-new-comment');
            const content = $ta.val().trim();
            if (!content) return;
            frappe.call({
                method: 'zhiz_print.api.template_store.add_template_comment',
                args: { template_id: tpl.name, content },
                callback: (r) => {
                    if ((r.message || {}).success) {
                        $ta.val('');
                        loadComments(tpl.name, dlg.$wrapper);
                        frappe.show_alert({ message: __('评论已提交'), indicator: 'green' });
                    } else {
                        frappe.show_alert({ message: __('评论失败'), indicator: 'red' });
                    }
                }
            });
        };
        dlg.$wrapper.on('click', '#pts-submit-comment', submitComment);
        // textarea 回车=换行,Ctrl/Cmd+Enter 才提交
        dlg.$wrapper.on('keydown', '#pts-new-comment', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); submitComment(); }
        });
        // dialog 关闭后销毁 DOM(bootstrap modal hide 不 remove,残留 #pts-* id 致下次 open 全局 selector 命中旧 dialog)
        dlg.$wrapper.on('hidden.bs.modal', () => { dlg.$wrapper.remove(); });
    }

    function loadPaperInfo(tpl, $ctx) {
        frappe.call({
            method: 'zhiz_print.api.template_store.get_template',
            args: { template_id: tpl.name },
            callback: (r) => {
                const t = r.message || {};
                let pc = {};
                try { pc = JSON.parse(t.print_paper_config || '{}'); } catch (e) {}
                $ctx.find('#pts-paper-info').html(
                    `<b>${__('单据')}</b>: ${__(t.target_doctype || '-')} &nbsp;·&nbsp; ` +
                    `<b>${__('纸张')}</b>: ${frappe.utils.escape_html(t.print_paper_name || '-')} ` +
                    `(${__('宽')} ${pc.width || '-'} × ${__('高')} ${pc.height || '-'}, ` +
                    `${__('边距')} T${pc.margin_top || '-'} B${pc.margin_bottom || '-'} L${pc.margin_left || '-'} R${pc.margin_right || '-'})`
                );
            }
        });
    }

    function loadComments(template_id, $ctx) {
        frappe.call({
            method: 'zhiz_print.api.template_store.list_template_comments',
            args: { template_id },
            callback: (r) => {
                const comments = (r.message || {}).comments || [];
                if (!comments.length) { $ctx.find('#pts-comments').html(`<div style="color:#aeaeb2;padding:8px;">${__('暂无评论')}</div>`); return; }
                $ctx.find('#pts-comments').html(comments.map(c =>
                    `<div class="pts-comment">
                        <span class="pts-comment-author">${frappe.utils.escape_html(c.author_display || __('匿名'))}</span>
                        <span class="pts-comment-date">${(c.created_at || '').slice(0, 16)}</span>
                        <div class="pts-comment-text">${frappe.utils.escape_html(c.content || '')}</div>
                    </div>`
                ).join(''));
            }
        });
    }

    function openInstallDialog(tpl) {
        const instDlg = new frappe.ui.Dialog({
            title: __('安装模板'),
            fields: [
                { fieldtype: 'Data', fieldname: 'new_design_name', label: __('设计名'), reqd: 1, default: tpl.template_name },
                { fieldtype: 'Data', fieldname: 'new_paper_name', label: __('纸张名(留空=沿用模板纸张)'), default: tpl.print_paper_name || '' },
            ],
            primary_action_label: __('确认安装'),
            primary_action: (v) => {
                frappe.call({
                    method: 'zhiz_print.api.template_store.install_template',
                    args: { template_id: tpl.name, new_design_name: v.new_design_name, new_paper_name: v.new_paper_name || null },
                    callback: (r) => {
                        const res = r.message || {};
                        if (res.success) {
                            instDlg.hide();
                            frappe.show_alert({ message: __('安装成功'), indicator: 'green' });
                            setTimeout(() => frappe.set_route('Form', 'Super Print Design', res.design_name), 600);
                        }
                    }
                });
            },
        });
        instDlg.show();
    }
}
