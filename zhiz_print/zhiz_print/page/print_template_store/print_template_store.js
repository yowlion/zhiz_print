// 智兆模板平台 - 独立 Page(浏览/预览/评论/安装平台打印模板)
// 数据走 zhiz_print.api.template_store.*(list/get/comment/install),后者再调 zhiz_licser template_api

frappe.pages['print-template-store'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({ parent: wrapper, title: __('模板平台'), single_column: false });
    $(wrapper).find('.page-form.row.hide').remove();
    // 面包屑:高级打印设计 > 模板平台。用 Frappe breadcrumbs API 注册到 all[current_page],route 变化/回退由 container.update() 自动渲染(原手动设 DOM 会被 update().clear() 覆盖)。
    // patch set_custom_breadcrumbs 支持 items 多级(原生只认单 route+label);仅对有 items 的生效,其他走原逻辑。
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

    // 顶部刷新按钮
    const $iconGroup = $(wrapper).find('.page-icon-group');
    $iconGroup.removeClass('hide hidden-xs hidden-sm')
        .html(`<button class="text-muted btn btn-default icon-btn" id="pts-reload-btn" title="${__('刷新')}">${frappe.utils.icon('es-line-reload', 'icon-sm')}</button>`);

    const $sidebar = $(wrapper).find('.layout-side-section');
    const $main = $(wrapper).find('.layout-main-section');

    // CSS
    $('head').append(`
    <style id="pts-layout">
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
        .pts-dlg-preview { width:100%; min-height:420px; border:1px solid #e8eaed; border-radius:8px; background:#f0f0f2; }
        .pts-paper-info { background:#f5f5f7; border-radius:8px; padding:8px 12px; font-size:12px; color:#6e6e73; margin:8px 0; }
        .pts-paper-info b { color:#1d1d1f; }
        .pts-comments { height:200px; overflow-y:auto; margin:6px 0; }
        .pts-comment { padding:6px 0; border-bottom:1px solid #f0f0f2; font-size:12px; }
        .pts-comment-author { font-weight:600; color:var(--zhiz-super-accent,#007AFF); }
        .pts-comment-date { color:#aeaeb2; font-size:10px; float:right; }
        .pts-comment-text { color:#1d1d1f; margin-top:2px; }
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

    $('#pts-reload-btn').on('click', () => loadTemplates());
    $('#pts-search').on('input', function () { keyword = $(this).val().toLowerCase().trim(); renderCards(); });
    $sidebar.on('click', '.pts-cat', function () {
        $sidebar.find('.pts-cat').removeClass('active');
        $(this).addClass('active');
        activeCategory = $(this).data('cat');
        renderCards();
    });

    loadTemplates();

    function loadTemplates() {
        $('#pts-grid').html(`<div class="pts-empty">${__('加载中...')}</div>`);
        frappe.call({
            method: 'zhiz_print.api.template_store.list_templates',
            args: {},
            callback: (r) => {
                const res = r.message || {};
                allTemplates = res.templates || [];
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
            `<div class="pts-cat" data-cat="${frappe.utils.escape_html(c)}">${frappe.utils.escape_html(c)} <span class="pts-cat-count">${counts[c]}</span></div>`
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
                    <div class="pts-card-meta">${frappe.utils.escape_html(t.target_doctype || '')} · v${t.version || 1} · ↓${t.download_count || 0}</div>
                    <div class="pts-card-co">${frappe.utils.escape_html(t.author_company || __('匿名'))}</div>
                </div>
            </div>`).join(''));

        // 顺序一致:iframes[i] ↔ list[i]
        const iframes = document.querySelectorAll('#pts-grid .pts-card-thumb iframe');
        list.forEach((t, i) => {
            const ifr = iframes[i];
            if (ifr && t.preview_html) {
                try {
                    const d = ifr.contentWindow.document;
                    d.open(); d.write(t.preview_html); d.close();
                    // 注入纸张效果 CSS(灰底/白纸 box-shadow/居中)
                    const st = d.createElement('style');
                    st.textContent = 'body{background:#f0f0f0 !important;margin:0 !important;padding:20px !important;} .print-pages-wrapper{margin:0 auto !important;} .print-page{background:#fff !important;box-shadow:0 2px 16px rgba(0,0,0,.12) !important;margin:0 0 20px 0 !important;}';
                    (d.head || d.documentElement).appendChild(st);
                    // 88e5b86 基础上修横向纸张遮挡:iframe width=纸张实际宽(容横向 952 不截) height 固定 1123(容多页)
                    // scale=min(0.21, thumbW/pw):窄/纵向纸张(pw<=794) 用 0.21(即 88e5b86 视觉,多页完整);宽纸张(952) 用 thumbW/pw(<0.21 满卡宽不遮挡右)
                    const firstPage = d.querySelector('.print-page');
                    const pw = (firstPage && firstPage.offsetWidth) ? firstPage.offsetWidth : 794;
                    const iframeW = pw + 40;  // +40 容 body padding(注入的 20*2),否则 page 右侧超出 iframe 被截
                    ifr.style.width = iframeW + 'px';
                    ifr.style.height = '1123px';
                    const thumbW = ifr.parentElement.offsetWidth || 167;
                    ifr.style.transform = 'translateX(-50%) scale(' + Math.min(0.21, thumbW / iframeW).toFixed(4) + ')';
                } catch (e) {}
            }
        });

        $('#pts-grid').off('click', '.pts-card').on('click', '.pts-card', function () {
            const name = $(this).data('name');
            const tpl = allTemplates.find(t => t.name === name);
            if (tpl) openPreview(tpl);
        });
    }

    function openPreview(tpl) {
        const dlg = new frappe.ui.Dialog({
            title: __(tpl.template_name),
            size: 'extra-large',
            fields: [
                { fieldtype: 'HTML', fieldname: 'preview',
                  options: `<div style="height:40vh;overflow:auto;background:#f0f0f0;border-radius:8px;"><iframe id="pts-dlg-iframe" class="pts-dlg-preview"></iframe></div>` },
                { fieldtype: 'HTML', fieldname: 'paper',
                  options: `<div class="pts-paper-info" id="pts-paper-info">${__('加载中...')}</div>` },
                { fieldtype: 'HTML', fieldname: 'actions_comments',
                  options: `<div style="display:flex;align-items:center;gap:12px;margin:4px 0;">
                              <button class="btn btn-success btn-sm" id="pts-install-btn"><i class="fa fa-download"></i> ${__('下载安装')}</button>
                              <span style="color:#86868b;font-size:12px;">${__('下载次数')}: <b style="color:#1d1d1f;">${tpl.download_count || 0}</b></span>
                            </div>
                            <hr style="margin:8px 0;">
                            <div style="font-weight:600;">${__('评论')}</div>
                            <div class="pts-comments" id="pts-comments">${__('加载中...')}</div>
                            <div class="input-group" style="margin-top:6px;">
                                <input type="text" id="pts-new-comment" class="form-control input-sm" placeholder="${__('留言...')}">
                                <span class="input-group-btn"><button class="btn btn-sm btn-default" id="pts-submit-comment">${__('提交')}</button></span>
                            </div>` },
            ],
        });
        // 写完整预览 + 注入纸张 CSS。时机:shown.bs.modal(modal 显示动画 ~300ms 完成后 iframe contentDocument 才稳定可写) + setTimeout 兜底 + iframe 未渲染重试
        const writePreview = () => {
            const ifr = dlg.$wrapper.find('#pts-dlg-iframe')[0];
            if (!ifr) { setTimeout(writePreview, 50); return; }
            try {
                const d = ifr.contentWindow.document;
                d.open(); d.write(tpl.preview_html || ''); d.close();
                const st = d.createElement('style');
                st.textContent = 'body{background:#f0f0f0 !important;margin:0 !important;padding:20px !important;} .print-pages-wrapper{margin:0 auto !important;} .print-page{background:#fff !important;box-shadow:0 2px 16px rgba(0,0,0,.12) !important;margin:0 0 20px 0 !important;}';
                (d.head || d.documentElement).appendChild(st);
            } catch (e) {}
            const resize = () => { try { const cd = ifr.contentWindow.document; const h = Math.max(cd.body.scrollHeight, cd.documentElement.scrollHeight, cd.body.offsetHeight); if (h > 0) ifr.style.height = (h + 16) + 'px'; } catch (e2) {} };
            [100, 500, 1500].forEach(ms => setTimeout(resize, ms));
        };
        dlg.show();
        dlg.$wrapper.on('shown.bs.modal', writePreview);
        setTimeout(writePreview, 100);
        setTimeout(writePreview, 600);

        loadPaperInfo(tpl, dlg.$wrapper);
        loadComments(tpl.name, dlg.$wrapper);

        // 事件绑定限定本 dialog(事件委托,避免全局 id 冲突)
        dlg.$wrapper.on('click', '#pts-install-btn', () => openInstallDialog(tpl));
        dlg.$wrapper.on('click', '#pts-submit-comment', () => {
            const content = dlg.$wrapper.find('#pts-new-comment').val().trim();
            if (!content) return;
            frappe.call({
                method: 'zhiz_print.api.template_store.add_template_comment',
                args: { template_id: tpl.name, content },
                callback: (r) => {
                    if ((r.message || {}).success) {
                        dlg.$wrapper.find('#pts-new-comment').val('');
                        loadComments(tpl.name, dlg.$wrapper);
                        frappe.show_alert({ message: __('评论已提交'), indicator: 'green' });
                    } else {
                        frappe.show_alert({ message: __('评论失败'), indicator: 'red' });
                    }
                }
            });
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
                    `<b>${__('单据')}</b>: ${frappe.utils.escape_html(t.target_doctype || '-')} &nbsp;·&nbsp; ` +
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
