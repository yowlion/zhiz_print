// Super Print Page - Enhanced Print View
// When enabled: template selector on left + preview in center
// When disabled: keep original Frappe print logic


frappe.ui.form.PrintView = class SuperPrintView extends frappe.ui.form.PrintView {

	// ==================== make() Override ====================
	// In the constructor, make() runs first - check route to determine if Super Print is enabled
	// If enabled, skip original UI creation to avoid old interface flickering

	make() {
		// Get current doctype from route
		const route = frappe.get_route();
		const doctype = route[1];
		const pd = frappe.boot.zhiz_print?.print_designer;

		// 报表模式:/app/print/Report/<report_name> — 仅当请求来自**报表查询页的拦截跳转**时接管
		// (拦截器 open_print_page 用整页跳转并带 spd_report_print=1 标记 + URL 筛选)。
		// Report 文档表单页(/app/report/<name>)的原生打印按钮不带此标记 → 走原生单据打印,
		// 不误入报表打印模式(v15.22.39)。
		const isFromReportView = new URLSearchParams(window.location.search).get('spd_report_print') === '1'
			|| (window.sessionStorage && sessionStorage.getItem('spd_report_print:' + (route[2] ? decodeURIComponent(route[2]) : '')) === '1');
		if (doctype === 'Report' && route[2] && isFromReportView) {
			if (pd && pd.report_enabled) {
				this.is_super_print_mode = true;
				this.is_report_mode = true;
				this.report_name = decodeURIComponent(route[2]);
				// 筛选来源:URL query 优先(拦截器直传,可见可分享) →
				// 按报表名分键的 sessionStorage 兜底 → 空(由服务端补默认筛选)
				const urlFilters = {};
				try {
					new URLSearchParams(window.location.search).forEach((v, k) => {
						if (v !== '') urlFilters[k] = v;
					});
				} catch (e) { /* ignore */ }
				let storedFilters = {};
				try {
					storedFilters = JSON.parse(sessionStorage.getItem('spd_report_filters:' + this.report_name) || '{}');
				} catch (e) { /* ignore */ }
				this.report_filters = Object.keys(urlFilters).length ? urlFilters : (storedFilters || {});
				this.wrapper = $(this.wrapper || []);
				this.print_settings = frappe.model.get_doc(":Print Settings", "Print Settings");
				return;
			}
		}

		let superPrintEnabled = false;
		if (pd && pd.enabled && doctype) {
			if (!pd.enable_mode || pd.enable_mode === 'Enable for All') {
				// Enable for All: 所有 doctype 都进新打印预览(不管有没有设计模板,无设计时进空模板列表可新建)
				superPrintEnabled = true;
			} else if (pd.enable_mode === 'Enable for Specific') {
				const list = pd.enabled_doctypes || [];
				// Specific: 列表内的 doctype 都进(不管有没有设计模板)
				superPrintEnabled = list.includes(doctype);
			}
		}

		if (superPrintEnabled) {
			this.is_super_print_mode = true;
			// Minimal init only, skip original UI creation
			this.wrapper = $(this.wrapper || []);
			this.print_settings = frappe.model.get_doc(":Print Settings", "Print Settings");
			return;
		}

		// Non-Super Print mode: use original make()
		super.make();
	}

	show(frm) {
		this.frm = frm;
		this.set_title();
		this.set_breadcrumbs();

		if (this.is_super_print_mode) {
			this.setup_super_print_page();
			return;
		}

		// Original logic
		this.is_super_print_mode = false;
		this.setup_customize_dialog();

		this.inner_msg = this.page.add_inner_message(`
			<a style="line-height: 2.4" href="/app/print-format-builder-beta?doctype=${this.frm.doctype}">
				${__("Try the new Print Format Builder")}
			</a>
		`);

		let tasks = [
			this.set_default_print_format,
			this.set_default_print_language,
			this.set_default_letterhead,
			this.preview,
		].map((fn) => fn.bind(this));

		this.setup_additional_settings();
		return frappe.run_serially(tasks);
	}

// ==================== Super Print Mode ====================

	async setup_super_print_page() {
		// Check license expiry
		const lic = frappe.boot.zhiz_print?.print_designer;
		if (lic && lic.expired) {
			this.page.main.html(`
				<div class="sp-expired-notice" style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:calc(100vh - 120px);color:#e74c3c;">
					<i class="fa fa-lock" style="font-size:48px;margin-bottom:20px"></i>
					<h3>${__('Subscription Expired')}</h3>
					<p class="text-muted" style="margin-top:10px;max-width:400px;text-align:center">${lic.license_message || __('Your subscription has expired. Please renew to continue using Super Print.')}</p>
					<a href="/app/zprint-setting" class="btn btn-primary" style="margin-top:20px">${__('Go to Settings')}</a>
				</div>
			`);
			return;
		}

		// Hide sidebar, clear main area
		if (this.page.sidebar) this.page.sidebar.hide();
		this.page.main.empty();

		// Override toolbar and menu
		this.setup_toolbar();
		this.setup_menu();

		// 模式徽标条:清晰区分「单据打印」/「报表打印」(v15.23)
		this.page.main.append(this._build_mode_badge());

		// Layout
		const enableNative = !!(frappe.boot.zhiz_print?.print_designer?.enable_native_print_formats) && !this.is_report_mode;
		this.page.main.html(`
			<div class="super-print-layout">
				<div class="super-print-sidebar" id="super-print-sidebar">
					<div class="sp-sidebar-top">
						<div class="sp-sidebar-header">
							<i class="fa fa-print"></i> 高级打印模板
						</div>
						<div class="sp-sidebar-body" id="sp-template-list">
							<div class="sp-loading"><i class="fa fa-spinner fa-spin"></i> ${__('Loading...')}</div>
						</div>
						${enableNative ? `
						<div class="sp-native-section" id="sp-native-section">
							<div class="sp-native-header" id="sp-native-header">
								<i class="fa fa-caret-right sp-native-caret"></i> 原生打印模板
							</div>
							<div class="sp-native-body" id="sp-native-list" style="display:none">
								<div class="sp-loading"><i class="fa fa-spinner fa-spin"></i> ${__('Loading...')}</div>
							</div>
						</div>` : ''}
					</div>
					<div class="sp-sidebar-bottom">
						<div class="sp-sidebar-header" id="sp-log-header">
							<i class="fa fa-history"></i> ${__('Printed')} <span id="sp-log-count">0</span> ${__('times')}
						</div>
						<div class="sp-sidebar-body sp-log-list" id="sp-log-list">
							<div class="sp-loading"><i class="fa fa-spinner fa-spin"></i></div>
						</div>
					</div>
				</div>
				<div class="super-print-main">
					<div class="sp-preview-area" id="sp-preview-area">
						<div class="sp-no-preview">
							<i class="fa fa-print" style="font-size:48px;color:#ccc"></i>
							<p class="text-muted">${__('Please select a print template from the left')}</p>
						</div>
					</div>
				</div>
			</div>
		`);

		// Load template list
		await this.load_templates();
		// Load native print formats (sidebar collapsible section, if enabled in settings)
		this.load_native_formats();
	}

	// ==================== Toolbar Override ====================

	// 模式徽标条:报表模式(青色 fa-table) / 单据模式(蓝色 fa-file-text-o) + 筛选摘要(v15.23)
	_build_mode_badge() {
		const div = document.createElement('div');
		if (this.is_report_mode) {
			const chips = Object.keys(this.report_filters || {})
				.filter(k => this.report_filters[k] !== '' && this.report_filters[k] != null)
				.map(k => `${__(k)}: ${this.report_filters[k]}`)
				.join('  ·  ');
			div.className = 'sp-mode-badge sp-mode-badge-report';
			div.style.cssText = 'display:flex;align-items:center;gap:10px;padding:7px 14px;background:#e0f2f1;border-bottom:1px solid #b2dfdb;font-size:12px;color:#00695c;';
			div.innerHTML =
				'<span style="display:inline-flex;align-items:center;gap:5px;background:#009688;color:#fff;border-radius:3px;padding:2px 8px;font-weight:600;white-space:nowrap;">' +
				'<i class="fa fa-table"></i> ' + __('Report Print') + '</span>' +
				'<b style="font-size:13px;color:#004d40;">' + this.escapeHtml(this.report_name || '') + '</b>' +
				(chips ? '<span style="color:#00796b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + this.escapeHtml(chips) + '"><i class="fa fa-filter"></i> ' + this.escapeHtml(chips) + '</span>' : '');
		} else {
			div.className = 'sp-mode-badge sp-mode-badge-doc';
			div.style.cssText = 'display:flex;align-items:center;gap:10px;padding:7px 14px;background:#e3f2fd;border-bottom:1px solid #bbdefb;font-size:12px;color:#1565c0;';
			div.innerHTML =
				'<span style="display:inline-flex;align-items:center;gap:5px;background:#1976d2;color:#fff;border-radius:3px;padding:2px 8px;font-weight:600;white-space:nowrap;">' +
				'<i class="fa fa-file-text-o"></i> ' + __('Document Print') + '</span>' +
				'<b style="font-size:13px;color:#0d47a1;">' + this.escapeHtml(this.frm?.doctype || '') + ' · ' + this.escapeHtml(this.frm?.docname || '') + '</b>';
		}
		return div;
	}

	_is_preview_only() {
		// v15.04.25: draft_no_print is a per-design toggle that blocks printing
		// when the *document* is in draft state (docstatus=0). Submitted docs
		// print normally even when the design has draft_no_print=1.
		// v15.23 报表模式:报表非单据,无 docstatus 草稿语义,不做拦截
		if (this.is_report_mode) return false;
		const designFlagged = this.current_design_info?.draft_no_print;
		const isDesignFlagged = designFlagged === 1 || designFlagged === '1' || designFlagged === true;
		const docstatus = parseInt(this.frm?.doc?.docstatus || 0, 10);
		return isDesignFlagged && docstatus === 0;
	}

	setup_toolbar() {
		if (!this.is_super_print_mode) {
			super.setup_toolbar();
			return;
		}

		// Clear all default buttons
		this.page.clear_primary_action();
		this.page.clear_custom_actions();
		this.page.clear_actions();
		this.page.clear_icons();
		$(this.page.inner_toolbar).find('.inner-page-message').remove();

		// Keep only: Print primary button + Export buttons (based on settings)
		// v15.04.25: hide Print / Export when doc is draft (docstatus=0) and design
		// has draft_no_print=1. Submitted documents always show full toolbar.
		if (this._is_preview_only()) {
			this.page.set_primary_action(__('Draft Document — Preview Only'), () => {
				frappe.show_alert({
					message: __('This document is in draft state (not submitted). Printing and export are disabled by the design\'s "Draft No Print" setting. Submit the document first.'),
					indicator: 'orange',
				});
			}, 'lock');
		} else {
			this.page.set_primary_action(__('Print'), () => this.printit(), 'printer');
			const pd = frappe.boot.zhiz_print?.print_designer;
			// Native format selected: PDF via frappe download_pdf, hide Excel (native has no excel)
			if (this.current_native_format) {
				if (pd?.allow_export_pdf !== false) {
					this.page.add_button(__('Export PDF'), () => this.native_download_pdf(), { icon: 'es-solid-pdf' });
				}
			} else {
				if (pd?.allow_export_pdf !== false) {
					this.page.add_button(__('Export PDF'), () => this.generate_super_pdf(), { icon: 'es-solid-pdf' });
				}
				if (pd?.allow_export_excel !== false) {
					this.page.add_button(__('Export Excel'), () => this.export_super_excel(), { icon: 'es-solid-excel' });
				}
			}
		}

		// Zoom controls
		const $toolbar = $(this.page.inner_toolbar);
		const zoomHtml = '<div class="sp-zoom-controls" style="display:inline-flex;align-items:center;gap:2px;margin-left:12px;padding-left:12px;border-left:1px solid #d0d0d0;">'
			+ '<button class="btn btn-xs btn-default sp-zoom-btn" id="sp-zoom-out" title="' + __('Zoom Out') + '"><i class="fa fa-minus"></i></button>'
			+ '<input type="number" id="sp-zoom-input" class="form-control" style="width:50px;height:24px;text-align:center;font-size:11px;padding:0 2px;" min="10" max="500" value="100">'
			+ '<span style="font-size:11px;color:#888;">%</span>'
			+ '<button class="btn btn-xs btn-default sp-zoom-btn" id="sp-zoom-in" title="' + __('Zoom In') + '"><i class="fa fa-plus"></i></button>'
			+ '<button class="btn btn-xs btn-default sp-zoom-btn" id="sp-zoom-reset" title="' + __('Reset Zoom') + '"><i class="fa fa-expand"></i></button>'
			+ '</div>';
		$toolbar.append(zoomHtml);
		$toolbar.find('#sp-zoom-in').on('click', () => this.adjust_zoom(10));
		$toolbar.find('#sp-zoom-out').on('click', () => this.adjust_zoom(-10));
		$toolbar.find('#sp-zoom-reset').on('click', () => this.reset_zoom());
		$toolbar.find('#sp-zoom-input').on('change', (e) => this.set_zoom(parseInt(e.target.value) || 100));
	}


	// ==================== Zoom Controls ====================

	adjust_zoom(delta) {
		const newZoom = Math.max(10, Math.min(500, (this.user_zoom || 100) + delta));
		this.set_zoom(newZoom);
	}

	set_zoom(value) {
		this.user_zoom = Math.max(10, Math.min(500, value));
		const input = document.getElementById('sp-zoom-input');
		if (input) input.value = this.user_zoom;
		this.apply_zoom();
	}

	reset_zoom() {
		this.set_zoom(100);
	}

	apply_zoom() {
		const container = document.querySelector('.sp-pages-container');
		if (!container || !this.base_scale) return;
		const finalScale = this.base_scale * (this.user_zoom / 100);
		container.style.transform = 'scale(' + finalScale + ')';
	}

	// ==================== Menu Override ====================

	setup_menu() {
		if (!this.is_super_print_mode) {
			super.setup_menu();
			return;
		}

		this.page.clear_menu();

		// Paper Settings → Open the paper document linked to current template
		this.page.add_menu_item(__('Paper Settings'), () => {
			if (this.current_native_format) {
				frappe.show_alert({ message: '原生打印模板无纸张设置', indicator: 'blue' });
				return;
			}
			const paper = this.current_design_info?.print_paper;
			if (paper) {
				frappe.set_route('Form', 'Super Print Paper', paper);
			} else {
				frappe.show_alert({ message: __('Please select a print template first'), indicator: 'yellow' });
			}
		});

		// Print Design → Open the currently selected print design document
		this.page.add_menu_item(__('Print Design'), () => {
			if (this.current_native_format) {
				frappe.show_alert({ message: '原生打印模板无高级设计', indicator: 'blue' });
				return;
			}
			if (this.current_design) {
				frappe.set_route('Form', 'Super Print Design', this.current_design);
			} else {
				frappe.show_alert({ message: __('Please select a print template first'), indicator: 'yellow' });
			}
		});
	}

	// ==================== Template List ====================

	async load_templates() {
		const listEl = document.getElementById('sp-template-list');
		try {
			// 报表模式:取该报表绑定的设计(design_target='Report')
			const res = this.is_report_mode
				? await frappe.call({
					method: 'zhiz_print.api.report_print.get_report_designs',
					args: { report_name: this.report_name }
				})
				: await frappe.call({
					method: 'zhiz_print.api.print_designer.get_available_designs',
					args: { doctype: this.frm.doctype, docname: this.frm.docname }
				});

			const designs = res.message || [];
			if (designs.length === 0) {
				listEl.innerHTML = `
					<div class="sp-empty">
						<p>${this.is_report_mode
							? __('No report designs found. Create one to start printing this report.')
							: __('No print templates available')}</p>
					</div>`;
				this._append_new_design_btn(listEl);
				return;
			}

			this.available_designs = designs;
			listEl.innerHTML = '';
			designs.forEach(d => {
				const item = document.createElement('div');
				item.className = 'sp-template-item';
				item.dataset.name = d.name;
				item.innerHTML = `<i class="fa fa-file-text-o"></i><span>${this.escapeHtml(d.design_name)}</span>`;
				item.addEventListener('click', () => this.on_template_click(d, item));
				listEl.appendChild(item);
			});

			// Show new design button at bottom even when templates exist
			this._append_new_design_btn(listEl);

			// Load print logs
			this.load_print_logs();

			// Auto-select first template
			const firstItem = listEl.querySelector('.sp-template-item');
			if (firstItem) {
				this.on_template_click(designs[0], firstItem);
			}

		} catch (e) {
			console.error('Failed to load print templates:', e);
			listEl.innerHTML = `<div class="sp-error"><i class="fa fa-exclamation-circle"></i> ${__('Loading failed')}</div>`;
		}
	}

	_append_new_design_btn(listEl) {
		const btn = document.createElement('div');
		btn.className = 'sp-new-design-btn';
		btn.innerHTML = '<i class="fa fa-plus"></i> ' + (this.is_report_mode ? __('Create Report Design') : __('New Print Design'));
		btn.addEventListener('click', () => {
			const hash = Math.random().toString(36).substring(2, 12);
			// 报表模式:预填 design_target=Report + report_name(设计器 onload 读取 route_options)
			frappe.route_options = this.is_report_mode
				? { design_target: 'Report', report_name: this.report_name }
				: { target_doctype: this.frm.doctype };
			frappe.set_route('Form', 'Super Print Design', 'new-super-print-design-' + hash);
		});
		listEl.appendChild(btn);
	}

	async on_template_click(design, el) {
		// Highlight selected (clear both super-design items and native-format items)
		document.querySelectorAll('.sp-template-item.active, .sp-native-item.active').forEach(i => i.classList.remove('active'));
		el.classList.add('active');
		// Switching back to a super design: clear native selection
		this.current_native_format = null;
		this.current_native_html = '';

		this.current_design = design.name;
		this.current_design_info = design;
		this.setup_toolbar();

		// Parameter dialog
		if (design.has_parameters && design.parameters?.length > 0) {
			const params = await this.show_parameter_dialog(design);
			if (params === null) return;
			this.current_params = params;
		} else {
			this.current_params = {};
		}

		// 勾选呈现的数据驱动行: 打印前逐个弹窗勾选(确认一个再弹下一个)
		if (design.select_data_rows && design.select_data_rows.length) {
			const selection = await this.collect_data_row_selection(design);
			if (selection === null) return;
			this.current_params.__row_selection = selection;
		}

		// Render preview
		await this.render_preview();
	}

	show_parameter_dialog(design) {
		return new Promise((resolve) => {
			let resolved = false;
			const fields = (design.parameters || []).map(p => ({
				fieldname: p.param_name,
				label: p.param_label || p.param_name,
				fieldtype: p.param_type || 'Data',
				default: p.default_value,
				reqd: p.reqd,
				options: p.options
			}));

			const dialog = new frappe.ui.Dialog({
				title: __('Print Parameters') + ' - ' + design.design_name,
				fields: fields,
				primary_action_label: 'OK',
				primary_action: (values) => {
					resolved = true;
					dialog.hide();
					resolve(values);
				}
			});

			dialog.get_secondary_btn().show();
			dialog.set_secondary_action_label(__('Cancel'));
			dialog.set_secondary_action(() => {
				dialog.hide();
			});

			dialog.onhide = () => {
				if (!resolved) resolve(null);
			};
			dialog.show();
		});
	}

	// ==================== Data Row Selection (勾选呈现) ====================

	async collect_data_row_selection(design) {
		// Sequentially prompt a checkbox-selection dialog for every select-mode
		// Data-Driven Row (confirm one → next pops). Returns {row: [key...]}
		// or null when cancelled. Default: all unchecked; at least one row
		// must be picked.
		const rows = design.select_data_rows || [];
		const selection = {};
		for (let i = 0; i < rows.length; i++) {
			const row = rows[i];
			let opts;
			try {
				const r = await frappe.call({
					method: 'zhiz_print.api.print_designer.get_data_row_options',
					args: {
						doctype: this.frm.doctype,
						docname: this.frm.docname,
						design_name: this.current_design,
						row: row,
						params: this.current_params || {}
					}
				});
				opts = r.message;
			} catch (e) {
				console.error('get_data_row_options failed:', e);
				frappe.msgprint(__('Failed to load data rows'));
				return null;
			}
			// No candidate data → nothing to pick, row renders empty anyway
			if (!opts || !opts.items || !opts.items.length) {
				selection[String(row)] = [];
				continue;
			}
			const keys = await this.show_data_selection_dialog(design, row, opts, i + 1, rows.length);
			if (keys === null) return null;
			selection[String(row)] = keys;
		}
		return selection;
	}

	show_data_selection_dialog(design, row, opts, seq, total) {
		return new Promise((resolve) => {
			let resolved = false;

			// Candidate rows shown with their rendered values — mirrors what
			// would print for that data item.
			const rowsHtml = opts.items.map(it => {
				const cells = (it.cells || []).map(c => '<td>' + this.escapeHtml(c) + '</td>').join('');
				return '<tr data-key="' + it.key + '">' +
					'<td style="text-align:center;width:34px"><input type="checkbox" class="sp-datarow-check"></td>' +
					'<td style="text-align:center;width:40px;color:#6c757d">' + it.seq + '</td>' + cells + '</tr>';
			}).join('');

			const dialog = new frappe.ui.Dialog({
				title: __('Select Rows to Print') + ' - ' + this.escapeHtml(design.design_name) +
					' <small class="text-muted">(' + __('Row') + ' ' + row + ', ' + seq + '/' + total + ')</small>',
				primary_action_label: __('Print Selected'),
				primary_action: () => {
					const keys = this._checked_data_keys(dialog);
					if (!keys.length) {
						frappe.show_alert({ message: __('Select at least one row'), indicator: 'yellow' });
						return;
					}
					resolved = true;
					dialog.hide();
					resolve(keys);
				}
			});

			dialog.$body.append(
				'<div style="margin-bottom:8px">' +
					'<label style="font-weight:normal;margin:0;cursor:pointer"><input type="checkbox" id="sp-datarow-checkall"> ' + __('Select All') + '</label>' +
					'<span id="sp-datarow-count" class="text-muted" style="margin-left:12px;font-size:12px"></span>' +
				'</div>' +
				'<div style="max-height:50vh;overflow:auto;border:1px solid #d1d8dd">' +
					'<table class="table table-condensed" style="margin:0;font-size:12px;table-layout:fixed">' +
						'<tbody>' + rowsHtml + '</tbody>' +
					'</table>' +
				'</div>');

			const updateCount = () => {
				const n = dialog.$body.find('.sp-datarow-check:checked').length;
				dialog.$body.find('#sp-datarow-count').text(__('Selected') + ': ' + n + '/' + opts.items.length);
				dialog.get_primary_btn().prop('disabled', n === 0);
			};
			dialog.$body.on('change', '.sp-datarow-check', updateCount);
			dialog.$body.find('#sp-datarow-checkall').on('change', function () {
				dialog.$body.find('.sp-datarow-check').prop('checked', this.checked);
				updateCount();
			});

			dialog.get_secondary_btn().show();
			dialog.set_secondary_action_label(__('Cancel'));
			dialog.set_secondary_action(() => dialog.hide());
			dialog.onhide = () => {
				if (!resolved) resolve(null);
			};

			dialog.show();
			updateCount(); // default: all unchecked → confirm disabled
		});
	}

	_checked_data_keys(dialog) {
		return dialog.$body.find('.sp-datarow-check:checked').map(function () {
			return Number($(this).closest('tr').data('key'));
		}).get();
	}

	// ==================== Preview Rendering ====================

	async render_preview() {
		if (!this.current_design) return;

		const area = document.getElementById('sp-preview-area');
		area.innerHTML = '<div class="sp-loading"><i class="fa fa-spinner fa-spin fa-2x" style="color:#2196f3"></i><p class="text-muted" style="margin-top:10px">' + __('Rendering preview...') + '</p></div>';

		const dimsOf = (msg) => ({
			paper_width: msg.paper_width, paper_height: msg.paper_height,
			margin_top: msg.margin_top, margin_bottom: msg.margin_bottom,
			margin_left: msg.margin_left, margin_right: msg.margin_right,
		});
		const callArgs = (extra) => Object.assign(this.is_report_mode ? {
			report_name: this.report_name,
			filters: this.report_filters || {},
			design_name: this.current_design,
			params: this.current_params || {},
		} : {
			doctype: this.frm.doctype,
			docname: this.frm.docname,
			design_name: this.current_design,
			params: this.current_params || {},
		}, extra || {});
		// 报表模式走 render_report_preview(注入 __report_main__ 报表行)
		const renderMethod = this.is_report_mode
			? 'zhiz_print.api.report_print.render_report_preview'
			: 'zhiz_print.api.print_designer.render_print_preview';

		// v15.10.02: measure-first, render precise ONCE — no estimation flash.
		// The spinner stays up until the precise HTML is ready; the estimation is
		// fetched only as a fallback if measurement / precise is unavailable.
		try {
			const r1 = await frappe.call({
				method: renderMethod,
				args: callArgs({ measurement_only: 1 })
			});
			if (r1.message && r1.message.measurement_html && r1.message.content_h_px) {
				const msg1 = r1.message;
				const measured = await this._measure_row_heights(msg1.measurement_html, msg1.content_w_px);
				if (measured && Object.keys(measured.heights).length) {
					const breakMap = this._compute_break_map(measured, msg1);
					if (breakMap) {
						const r2 = await frappe.call({
							method: renderMethod,
							args: callArgs({ page_break_map: breakMap.page_break_map, row_heights: breakMap.row_heights, shrink_map: breakMap.shrink_map })
						});
						if (r2.message && r2.message.precise) {
							this.current_preview_html = r2.message.html;
							this.current_break_map = breakMap;
							this._render_pages_html_into_preview(r2.message.html, dimsOf(r2.message));
							return;
						}
					}
				}
			}
			// Precise unavailable → estimation fallback
			console.warn('zhiz_print: precise pagination unavailable, falling back to estimation');
			await this._render_estimation_fallback(callArgs);
		} catch (e) {
			console.error('Preview render failed:', e);
			try {
				await this._render_estimation_fallback(callArgs);
			} catch (e2) {
				area.innerHTML = '<div class="alert alert-danger" style="margin:20px"><i class="fa fa-exclamation-circle"></i> Preview render failed: ' + this.escapeHtml(e.message || String(e)) + '</div>';
			}
		}
	}

	// Estimation fallback: fetched only when the precise (measured) path is unavailable.
	async _render_estimation_fallback(callArgs) {
		const r = await frappe.call({
			method: this.is_report_mode
				? 'zhiz_print.api.report_print.render_report_preview'
				: 'zhiz_print.api.print_designer.render_print_preview',
			args: callArgs({})
		});
		if (r.message && r.message.html) {
			this.current_preview_html = r.message.html;
			this.current_break_map = null;
			this._render_pages_html_into_preview(r.message.html, {
				paper_width: r.message.paper_width, paper_height: r.message.paper_height,
				margin_top: r.message.margin_top, margin_bottom: r.message.margin_bottom,
				margin_left: r.message.margin_left, margin_right: r.message.margin_right,
			});
		}
	}

	// Render a server-produced print HTML (one or more .print-page) into the preview area.
	_render_pages_html_into_preview(html, dims) {
		const area = document.getElementById('sp-preview-area');
		const { paper_width, paper_height, margin_top, margin_bottom, margin_left, margin_right } = dims || {};

		const PX_PER_MM = 4;
		const previewW = (paper_width || 210) * PX_PER_MM;
		const previewH = (paper_height || 297) * PX_PER_MM;
		const mTop = (margin_top || 0) * PX_PER_MM;
		const mBottom = (margin_bottom || 0) * PX_PER_MM;
		const mLeft = (margin_left || 0) * PX_PER_MM;
		const mRight = (margin_right || 0) * PX_PER_MM;
		const containerWidth = area.offsetWidth - 40;
		const scale = Math.min(1, containerWidth / previewW);
		this.base_scale = scale;
		if (!this.user_zoom) this.user_zoom = 100;
		const finalScale = scale * (this.user_zoom / 100);

		const parser = new DOMParser();
		const parsed = parser.parseFromString(html, 'text/html');
		const printPages = parsed.querySelectorAll('.print-page');

		area.innerHTML = '';
		const pagesContainer = document.createElement('div');
		pagesContainer.className = 'sp-pages-container';
		pagesContainer.style.cssText = 'transform:scale(' + finalScale + ');transform-origin:top center;display:flex;flex-direction:column;align-items:center;gap:20px;padding-bottom:20px;';

		const pageWrappers = [];

		if (printPages.length > 0) {
			// Multi-page mode: each page gets its own paper container
			printPages.forEach((pageEl, idx) => {
				const pageWrapper = document.createElement('div');
				pageWrapper.className = 'sp-paper-wrapper';
				pageWrapper.style.cssText =
					'width:' + previewW + 'px;' +
					'height:' + previewH + 'px;' +
					'background:white;' +
					'box-shadow:0 2px 16px rgba(0,0,0,.12);' +
					'position:relative;' +
					'overflow:hidden;' +
					'flex-shrink:0;';

				const marginLine = document.createElement('div');
				marginLine.className = 'sp-margin-line';
				marginLine.style.cssText =
					'position:absolute;' +
					'top:' + mTop + 'px;left:' + mLeft + 'px;' +
					'right:' + mRight + 'px;bottom:' + mBottom + 'px;' +
					'border:1px dashed rgba(0,120,215,0.4);' +
					'pointer-events:none;z-index:10;';
				pageWrapper.appendChild(marginLine);

				const iframe = document.createElement('iframe');
				iframe.className = 'sp-iframe';
				iframe.style.cssText = 'width:100%;height:100%;border:none;overflow:hidden;';
				iframe.scrolling = 'no';
				pageWrapper.appendChild(iframe);

				pagesContainer.appendChild(pageWrapper);
				pageWrappers.push({ iframe, pageEl });
			});
		} else {
			const pageWrapper = document.createElement('div');
			pageWrapper.className = 'sp-paper-wrapper';
			pageWrapper.style.cssText =
				'width:' + previewW + 'px;' +
				'min-height:' + previewH + 'px;' +
				'background:white;' +
				'box-shadow:0 2px 16px rgba(0,0,0,.12);' +
				'position:relative;';

			const marginLine = document.createElement('div');
			marginLine.className = 'sp-margin-line';
			marginLine.style.cssText =
				'position:absolute;' +
				'top:' + mTop + 'px;left:' + mLeft + 'px;' +
				'right:' + mRight + 'px;bottom:' + mBottom + 'px;' +
				'border:1px dashed rgba(0,120,215,0.4);' +
				'pointer-events:none;z-index:10;';
			pageWrapper.appendChild(marginLine);

			const iframe = document.createElement('iframe');
			iframe.className = 'sp-iframe';
			iframe.style.cssText = 'width:100%;height:' + previewH + 'px;border:none;';
			pageWrapper.appendChild(iframe);
			pagesContainer.appendChild(pageWrapper);
			pageWrappers.push({ iframe, pageEl: null });
		}

		area.appendChild(pagesContainer);

		requestAnimationFrame(() => {
			pageWrappers.forEach(({ iframe, pageEl }) => {
				const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
				iframeDoc.open();
				iframeDoc.write(pageEl
					? this._build_single_page_html(pageEl, parsed, previewW, previewH)
					: html);
				iframeDoc.close();
			});
		});
	}

	// v15.10.01: render the measurement scaffold off-screen and record each row's real
	// offsetHeight (ground truth from the browser layout engine) keyed by (page_no, serial).
	_timeout(ms) { return new Promise(r => setTimeout(r, ms)); }

	async _measure_row_heights(measurement_html, content_w_px) {
		const mframe = document.createElement('iframe');
		mframe.className = 'sp-measure-iframe';
		mframe.style.cssText = 'position:absolute;left:-99999px;top:0;width:' + (content_w_px || 800) + 'px;height:0;border:0;opacity:0;pointer-events:none;';
		document.body.appendChild(mframe);
		try {
			const mdoc = mframe.contentDocument || mframe.contentWindow.document;
			mdoc.open();
			mdoc.write(measurement_html);
			mdoc.close();
			// Wait for fonts (avoid height drift after font swap) + a layout pass
			if (mdoc.fonts && mdoc.fonts.ready) {
				await Promise.race([mdoc.fonts.ready, this._timeout(2000)]);
			} else {
				await this._timeout(300);
			}
			await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

			// 量 Auto Shrink 精确字号(measureText)并应用到 iframe cell,让后续行高基于精确字号
			const shrinks = {};
			const fontFamily = (this.frm && this.frm.doc && this.frm.doc.font_family) || 'Microsoft YaHei';
			const _canvas = document.createElement('canvas');
			const _ctx = _canvas.getContext('2d');
			mdoc.querySelectorAll('td[data-shrink-cell]').forEach(td => {
				const key = td.dataset.shrinkCell;
				const baseFs = parseInt(td.dataset.baseFs, 10) || 12;
				const cellW = parseInt(td.dataset.cellW, 10) || 0;
				const text = (td.textContent || '').trim();
				if (!key || !text || !cellW) return;
				_ctx.font = baseFs + 'px ' + fontFamily;
				const textW = _ctx.measureText(text).width;
				if (textW > cellW) {
					// -1px 安全余量: measureText(canvas) vs 浏览器 td 渲染有 sub-pixel/字体度量差异,
					// 临界字号(算出刚好=avail)实际渲染会略超被 overflow 截,减 1 避免末尾字符遮挡
					const fs = Math.max(6, Math.floor(baseFs * cellW / textW) - 1);
					if (fs < baseFs) {
						td.style.fontSize = fs + 'px';
						shrinks[key] = fs;
					}
				}
			});

			const heights = {}, kinds = {}, order = {}, links = {};
			const trs = mdoc.querySelectorAll('tr[data-serial]');
			trs.forEach(tr => {
				const pg = parseInt(tr.dataset.pg, 10);
				const serial = parseInt(tr.dataset.serial, 10);
				if (!heights[pg]) { heights[pg] = {}; kinds[pg] = {}; order[pg] = []; links[pg] = []; }
				heights[pg][serial] = tr.offsetHeight;
				kinds[pg][serial] = tr.dataset.kind || 'data';
				order[pg].push(serial);
				// Capture rowspan links so merged-cell groups stay atomic during packing
				tr.querySelectorAll('td[rowspan]').forEach(td => {
					const rs = parseInt(td.getAttribute('rowspan') || '1', 10);
					if (rs > 1) links[pg].push([serial, rs]);
				});
			});
			return { heights, kinds, order, links, shrinks };
		} finally {
			if (mframe.parentNode) mframe.parentNode.removeChild(mframe);
		}
	}

	// Greedy bin-pack: title rows repeat on every page; data rows flow and break when the
	// next row (or atomic rowspan group) no longer fits the measured content area.
	_compute_break_map(measured, msg) {
		const { heights, kinds, order, links } = measured;
		const content_h_px = msg.content_h_px;
		const page_count = msg.page_count || Object.keys(heights).length || 1;
		const page_break_map = {};
		const row_heights = {};

		for (let pg = 1; pg <= page_count; pg++) {
			const H = heights[pg];
			if (!H) continue;
			const ord = order[pg] || [];
			const titleSerials = ord.filter(s => kinds[pg][s] === 'title');
			const dataSerials = ord.filter(s => kinds[pg][s] !== 'title');
			const titleH = titleSerials.reduce((a, s) => a + (H[s] || 0), 0);

			// Per-row locked heights (all serials) for the server rebuild
			const rh = {};
			ord.forEach(s => { rh[s] = H[s] || 0; });
			row_heights[pg] = rh;

			const groupOf = this._build_rowspan_groups(dataSerials, links[pg] || []);
			const avail = content_h_px - titleH - 1; // -1 for table top border (parity with server)
			const pages = [];
			let cur = [], curH = 0, consumed = new Set();
			for (let i = 0; i < dataSerials.length; i++) {
				const s = dataSerials[i];
				if (consumed.has(s)) continue;
				const grp = groupOf[s] || [s];
				grp.forEach(g => consumed.add(g));
				const grpH = grp.reduce((a, g) => a + (H[g] || 0), 0);
				if (cur.length && curH + grpH > avail) {
					pages.push(cur); cur = []; curH = 0;
				}
				cur = cur.concat(grp); curH += grpH;
			}
			if (cur.length) pages.push(cur);
			if (!pages.length) pages.push([]);
			page_break_map[pg] = pages;
		}
		return { page_break_map, row_heights, shrink_map: measured.shrinks || {} };
	}

	// Union-find over data serials: rows sharing a rowspan cell become one atomic group.
	_build_rowspan_groups(dataSerials, links) {
		const parent = {};
		dataSerials.forEach(s => { parent[s] = s; });
		const find = x => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
		const union = (a, b) => { parent[find(a)] = find(b); };
		links.forEach(([start, span]) => {
			if (!parent.hasOwnProperty(start)) return; // rowspan rooted on a title row: ignore
			for (let k = 1; k < span; k++) {
				const nxt = start + k;
				if (parent.hasOwnProperty(nxt)) union(start, nxt);
			}
		});
		const byRoot = {};
		dataSerials.forEach(s => { const r = find(s); (byRoot[r] = byRoot[r] || []).push(s); });
		const groupOf = {};
		dataSerials.forEach(s => { groupOf[s] = byRoot[find(s)]; });
		return groupOf;
	}

	_build_single_page_html(pageEl, fullDoc, previewW, previewH) {
		// Extract original <style> tags
		const styles = fullDoc.querySelectorAll('style');
		let styleHtml = '';
		styles.forEach(s => { styleHtml += s.outerHTML; });

		return '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n' +
			styleHtml +
			'\n<style>\n@page { size: ' + (previewW / 4) + 'mm ' + (previewH / 4) + 'mm; margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }\n' +
			'* { box-sizing: border-box; }\nbody { margin: 0; padding: 0; }\n</style>\n' +
			'</head>\n<body>\n' + pageEl.outerHTML + '\n</body>\n</html>';
	}

	// ==================== Print Log ====================

	async load_print_logs() {
		try {
			const res = await frappe.call({
				method: 'zhiz_print.api.print_designer.get_print_log_list',
				// 报表日志按 Report/报表名 归组(record_print_log 的兼容策略)
				args: this.is_report_mode
					? { doctype: 'Report', docname: this.report_name }
					: { doctype: this.frm.doctype, docname: this.frm.docname }
			});

			const data = res.message || {};
			const logs = data.logs || [];
			const totalCount = data.total_count || 0;

			// Update count
			const countEl = document.getElementById('sp-log-count');
			if (countEl) countEl.textContent = totalCount;

			// Update log list
			const listEl = document.getElementById('sp-log-list');
			if (!listEl) return;

			if (logs.length === 0) {
				listEl.innerHTML = '<div class="sp-empty-log">' + __('No print records') + '</div>';
				return;
			}

			listEl.innerHTML = '';
			logs.forEach(log => {
				const item = document.createElement('div');
				item.className = 'sp-log-item';

				const time = log.print_time ? frappe.datetime.str_to_user(log.print_time) : '';
				const userFullName = log.print_user ? log.print_user : '';
				const userImg = log.print_user ? frappe.avatar(log.print_user) : '';

				// Select icon and color based on export type
				const exportType = (log.export_type || 'Print').trim();
				let typeIcon = 'fa-print';
				let typeColor = '#2196f3';
				if (exportType === 'Export PDF') {
					typeIcon = 'fa-file-pdf-o';
					typeColor = '#e53935';
				} else if (exportType === 'Export Excel') {
					typeIcon = 'fa-file-excel-o';
					typeColor = '#43a047';
				}

				item.innerHTML =
					'<div class="sp-log-line1">' +
						'<span class="sp-log-count">#' + this.escapeHtml(String(log.print_count)) + '</span>' +
						'<i class="fa ' + typeIcon + ' sp-log-type-icon" style="color:' + typeColor + '" title="' + this.escapeHtml(exportType) + '"></i>' +
						'<span class="sp-log-design">' + this.escapeHtml(log.print_design || '') + '</span>' +
					'</div>' +
					'<div class="sp-log-line2">' +
						userImg +
						'<span class="sp-log-user">' + this.escapeHtml(userFullName) + '</span>' +
					'</div>' +
					'<div class="sp-log-line3">' +
						'<i class="fa fa-clock-o"></i> ' + this.escapeHtml(time) +
					'</div>';

				item.addEventListener('click', () => {
					window.open('/app/super-print-log/' + encodeURIComponent(log.name), '_blank');
				});

				listEl.appendChild(item);
			});
		} catch (e) {
			console.error('Failed to load print logs:', e);
		}
	}

	// ==================== Print / PDF / Excel ====================

	// 日志参数辅助(v15.23):报表模式走 log_type='Report Print' + report_name + 筛选快照;
	// 单据模式保持原参数。design_label 允许调用方覆盖(如 '原生: xxx',仅单据模式)。
	_log_args(export_type, design_label) {
		if (this.is_report_mode) {
			return {
				design_name: design_label || this.current_design,
				log_type: 'Report Print',
				report_name: this.report_name,
				filters_used: JSON.stringify(this.report_filters || {}),
				params: this.current_params || {},
				preview_html: this.current_preview_html || '',
				export_type: export_type,
			};
		}
		return {
			doctype: this.frm.doctype,
			docname: this.frm.docname,
			design_name: design_label || this.current_design,
			params: this.current_params || {},
			preview_html: this.current_preview_html || '',
			export_type: export_type,
		};
	}

	printit() {
		if (this.is_super_print_mode) {
			if (this.current_native_format) {
				this.native_printit();
			} else {
				this.super_printit();
			}
		} else {
			super.printit();
		}
	}

	async super_printit() {
		if (this._is_preview_only()) {
			frappe.show_alert({
				message: __('This design is marked as draft. Preview only — printing is disabled.'),
				indicator: 'orange',
			});
			return;
		}
		if (!this.current_preview_html) {
			frappe.show_alert({ message: __('Please select a print template first'), indicator: 'yellow' });
			return;
		}

		// Record print log (includes preview HTML snapshot)
		try {
			await frappe.call({
				method: 'zhiz_print.api.print_designer.record_print_log',
				args: this._log_args('Print')
			});
		} catch (e) {
			console.error('Failed to record print log:', e);
		}

		// Print via hidden iframe (no new window)
		const printFrame = document.createElement('iframe');
		printFrame.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:0;height:0;border:none;';
		document.body.appendChild(printFrame);

		// v15.04.39: 浏览器打印直接使用 HTML 原始 @page(paper 原始宽高),
		// 不再做 Force Landscape/Portrait 交换 — 多页打印方式已解决方向问题。
		let printHtml = this._expand_print_count(this.current_preview_html);

		const frameDoc = printFrame.contentDocument || printFrame.contentWindow.document;
		frameDoc.open();
		frameDoc.write(printHtml);
		frameDoc.close();

		// Wait for content to load before calling print
		printFrame.onload = () => {
			setTimeout(() => {
				printFrame.contentWindow.print();
				// Remove iframe after print dialog closes
				setTimeout(() => {
					document.body.removeChild(printFrame);
				}, 1000);
			}, 300);
		};

		this.load_print_logs();
	}

	_expand_print_count(html) {
		// 打印次数驱动:把每个 print-page 按 data-print-count 复制 N 份(预览不重复,仅打印时)
		if (!html) return html;
		let doc;
		try { doc = new DOMParser().parseFromString(html, 'text/html'); }
		catch (e) { return html; }
		const pages = doc.querySelectorAll('.print-page');
		if (!pages.length) return html;
		const out = doc.createElement('div');
		pages.forEach(page => {
			let n = parseInt(page.dataset.printCount || '1', 10);
			if (isNaN(n) || n < 1) n = 1;  // 非数字/0 → 1(兜底)
			for (let i = 0; i < n; i++) out.appendChild(page.cloneNode(true));
		});
		return '<!DOCTYPE html>\n<html>\n<head>' + doc.head.innerHTML + '\n</head>\n<body>\n' + out.innerHTML + '\n</body>\n</html>';
	}

	// ==================== Native Print Format ====================

	toggle_native_section() {
		const body = document.getElementById('sp-native-list');
		const caret = document.querySelector('.sp-native-caret');
		if (!body) return;
		const open = body.style.display !== 'none';
		body.style.display = open ? 'none' : 'block';
		if (caret) {
			caret.classList.toggle('fa-caret-right', open);
			caret.classList.toggle('fa-caret-down', !open);
		}
		// 展开且当前未选原生 → 自动选最高优先级原生(is_default 优先,否则第一个)
		if (!open && !this.current_native_format && this.available_native_formats && this.available_native_formats.length) {
			const def = this.available_native_formats.find(f => f.is_default) || this.available_native_formats[0];
			const defItem = def && body.querySelector('.sp-native-item[data-name="' + def.name + '"]');
			if (defItem) this.on_native_template_click(def, defItem);
		}
	}

	async load_native_formats() {
		const section = document.getElementById('sp-native-section');
		const header = document.getElementById('sp-native-header');
		const listEl = document.getElementById('sp-native-list');
		if (!section || !listEl) return;  // native disabled in Zprint Setting
		if (header) header.addEventListener('click', () => this.toggle_native_section());
		try {
			const res = await frappe.call({
				method: 'zhiz_print.api.print_designer.get_native_print_formats',
				args: { doctype: this.frm.doctype, docname: this.frm.docname }
			});
			const formats = res.message || [];
			if (formats.length === 0) {
				listEl.innerHTML = '<div class="sp-empty">' + __('No print templates available') + '</div>';
				return;
			}
			this.available_native_formats = formats;
			listEl.innerHTML = '';
			formats.forEach(f => {
				const item = document.createElement('div');
				item.className = 'sp-native-item';
				item.dataset.name = f.name;
				item.innerHTML = '<i class="fa fa-file-o"></i><span>' + this.escapeHtml(f.label) + '</span>';
				item.addEventListener('click', () => this.on_native_template_click(f, item));
				listEl.appendChild(item);
			});
			// 无高级设计时,自动选最高优先级原生(is_default 优先,否则第一个)
			if ((!this.available_designs || this.available_designs.length === 0) && !this.current_native_format && !this.current_design) {
				const def = formats.find(f => f.is_default) || formats[0];
				const defItem = def && listEl.querySelector('.sp-native-item[data-name="' + def.name + '"]');
				if (defItem) this.on_native_template_click(def, defItem);
			}
		} catch (e) {
			console.error('Failed to load native formats:', e);
			listEl.innerHTML = '<div class="sp-error"><i class="fa fa-exclamation-circle"></i> ' + __('Loading failed') + '</div>';
		}
	}

	async on_native_template_click(format, el) {
		// Highlight selected (clear both super-design items and native-format items)
		document.querySelectorAll('.sp-template-item.active, .sp-native-item.active').forEach(i => i.classList.remove('active'));
		el.classList.add('active');

		this.current_native_format = format.name;
		this.current_native_html = '';
		this.current_design = null;
		this.current_design_info = null;
		this.current_params = {};
		this.setup_toolbar();
		this.setup_menu();

		await this.render_native_preview();
	}

	async render_native_preview() {
		if (!this.current_native_format) return;
		const area = document.getElementById('sp-preview-area');
		if (area) area.innerHTML = '<div class="sp-loading"><i class="fa fa-spinner fa-spin fa-2x" style="color:#2196f3"></i><p class="text-muted" style="margin-top:10px">' + __('Rendering preview...') + '</p></div>';
		try {
			const res = await frappe.call({
				method: 'zhiz_print.api.print_designer.render_native_print_preview',
				args: {
					doctype: this.frm.doctype,
					docname: this.frm.docname,
					print_format: this.current_native_format
				}
			});
			if (res.message && res.message.html) {
				this.current_native_html = res.message.html;
				// Native HTML is a full page (head+body); inject into a wrapper —
				// browser keeps <style> active and renders body content.
				if (area) {
					// iframe 隔离:原生 print HTML 含全局 <style>,直接注入 innerHTML 会泄漏到
					// 整个 desk(把顶部 navbar 挤到第二行)。用 srcdoc 让 <style> 仅在 iframe 内生效。
					area.innerHTML = '';
					const frame = document.createElement('iframe');
					frame.className = 'sp-native-preview-frame';
					frame.style.cssText = 'width:100%;min-height:600px;border:1px solid #e0e0e0;box-shadow:0 2px 12px rgba(0,0,0,.08);background:#fff;';
					frame.srcdoc = res.message.html;
					area.appendChild(frame);
				}
			} else {
				if (area) area.innerHTML = '<div class="alert alert-danger" style="margin:20px">' + __('Preview render failed') + '</div>';
			}
		} catch (e) {
			console.error('Native preview render failed:', e);
			if (area) area.innerHTML = '<div class="alert alert-danger" style="margin:20px"><i class="fa fa-exclamation-circle"></i> ' + this.escapeHtml(e.message || String(e)) + '</div>';
		}
	}

	async native_printit() {
		if (!this.current_native_html) {
			frappe.show_alert({ message: __('Please select a print template first'), indicator: 'yellow' });
			return;
		}
		// Record print log (native format → print_design stored as "原生: <format>")
		try {
			await frappe.call({
				method: 'zhiz_print.api.print_designer.record_print_log',
				args: {
					doctype: this.frm.doctype,
					docname: this.frm.docname,
					design_name: '原生: ' + this.current_native_format,
					params: {},
					preview_html: this.current_native_html,
					export_type: 'Print'
				}
			});
		} catch (e) {
			console.error('Failed to record native print log:', e);
		}

		// Print via hidden iframe (no new window)
		const printFrame = document.createElement('iframe');
		printFrame.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:0;height:0;border:none;';
		document.body.appendChild(printFrame);
		const frameDoc = printFrame.contentDocument || printFrame.contentWindow.document;
		frameDoc.open();
		frameDoc.write(this.current_native_html);
		frameDoc.close();
		printFrame.onload = () => {
			setTimeout(() => {
				printFrame.contentWindow.print();
				setTimeout(() => { document.body.removeChild(printFrame); }, 1000);
			}, 300);
		};
		this.load_print_logs();
	}

	async native_download_pdf() {
		if (!this.current_native_format) {
			frappe.show_alert({ message: __('Please select a print template first'), indicator: 'yellow' });
			return;
		}
		// frappe built-in PDF download
		const url = '/api/method/frappe.utils.print_format.download_pdf?doctype='
			+ encodeURIComponent(this.frm.doctype) + '&name=' + encodeURIComponent(this.frm.docname)
			+ '&format=' + encodeURIComponent(this.current_native_format) + '&no_letterhead=0';
		const w = window.open(url, '_blank');
		if (!w) {
			frappe.msgprint(__('Please allow pop-up windows'));
			return;
		}
		// Record PDF export log
		try {
			await frappe.call({
				method: 'zhiz_print.api.print_designer.record_print_log',
				args: {
					doctype: this.frm.doctype,
					docname: this.frm.docname,
					design_name: '原生: ' + this.current_native_format,
					params: {},
					preview_html: this.current_native_html || '',
					export_type: 'Export PDF'
				}
			});
			this.load_print_logs();
		} catch (e) {
			console.error('Failed to record native PDF log:', e);
		}
	}

	async generate_super_pdf() {
		if (this._is_preview_only()) {
			frappe.show_alert({
				message: __('This design is marked as draft. Preview only — PDF export is disabled.'),
				indicator: 'orange',
			});
			return;
		}
		if (!this.current_design) {
			frappe.show_alert({ message: __('Please select a print template first'), indicator: 'yellow' });
			return;
		}

		// 报表模式:筛选可能超 GET URL 长度,改 POST(XHR blob 下载)走 generate_report_pdf
		if (this.is_report_mode) {
			await this._report_pdf_post();
			return;
		}

		const params = new URLSearchParams({
			doctype: this.frm.doctype,
			docname: this.frm.docname,
			design_name: this.current_design,
			params: JSON.stringify(this.current_params || {}),
		});
		// v15.10.01: forward client-measured pagination so PDF breaks match the preview
		if (this.current_break_map) {
			params.set('page_break_map', JSON.stringify(this.current_break_map.page_break_map));
			params.set('row_heights', JSON.stringify(this.current_break_map.row_heights));
			if (this.current_break_map.shrink_map) params.set('shrink_map', JSON.stringify(this.current_break_map.shrink_map));
		}
		const url = '/api/method/zhiz_print.api.print_designer.generate_print_pdf?' + params;
		const w = window.open(url, '_blank');
		if (!w) {
			frappe.msgprint(__('Please allow pop-up windows'));
			return;
		}

		// Record PDF export log
		this.record_export_log('Export PDF');
	}

	// 报表二进制导出通用方法:POST JSON → blob 下载(筛选不落 GET URL)
	async _report_post_download(method_path, payload, filename) {
		const blob = await new Promise((resolve, reject) => {
			const xhr = new XMLHttpRequest();
			xhr.open('POST', '/api/method/' + method_path, true);
			xhr.responseType = 'blob';
			xhr.setRequestHeader('Content-Type', 'application/json');
			xhr.setRequestHeader('X-Frappe-CSRF-Token', frappe.csrf_token);
			xhr.onload = function () {
				if (xhr.status === 200) {
					const ct = xhr.getResponseHeader('Content-Type') || '';
					if (ct.indexOf('json') !== -1 || ct.indexOf('text/html') !== -1) {
						// JSON 错误响应
						const reader = new FileReader();
						reader.onload = function () {
							try {
								const err = JSON.parse(reader.result);
								reject(new Error(err.exception || err.message || __('Export failed')));
							} catch (e) {
								reject(new Error(reader.result || __('Export failed')));
							}
						};
						reader.readAsText(xhr.response);
					} else {
						resolve(xhr.response);
					}
				} else {
					reject(new Error(__('Export failed (HTTP {0})', [xhr.status])));
				}
			};
			xhr.onerror = function () { reject(new Error(__('Network error'))); };
			xhr.send(JSON.stringify(payload));
		});
		const blobUrl = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = blobUrl;
		a.download = filename;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(blobUrl);
	}

	// 报表 PDF:POST generate_report_pdf(XHR blob),转发实测分页参数与预览一致
	async _report_pdf_post() {
		frappe.show_alert({ message: __('Generating PDF...'), indicator: 'blue' });
		const payload = {
			report_name: this.report_name,
			filters: this.report_filters || {},
			design_name: this.current_design,
			params: this.current_params || {},
		};
		if (this.current_break_map) {
			payload.page_break_map = JSON.stringify(this.current_break_map.page_break_map);
			payload.row_heights = JSON.stringify(this.current_break_map.row_heights);
			if (this.current_break_map.shrink_map) payload.shrink_map = JSON.stringify(this.current_break_map.shrink_map);
		}
		try {
			await this._report_post_download(
				'zhiz_print.api.report_print.generate_report_pdf',
				payload,
				this.report_name + '-' + (this.current_design_info?.design_name || 'report') + '.pdf'
			);
			frappe.show_alert({ message: __('PDF exported'), indicator: 'green' });
			this.record_export_log('Export PDF');
		} catch (e) {
			console.error('Report PDF export failed:', e);
			frappe.show_alert({ message: e.message || __('PDF export failed'), indicator: 'red' });
		}
	}


	async export_super_excel() {
		if (this._is_preview_only()) {
			frappe.show_alert({
				message: __('This design is marked as draft. Preview only — Excel export is disabled.'),
				indicator: 'orange',
			});
			return;
		}
		if (!this.current_design) {
			frappe.show_alert({ message: __('Please select a print template first'), indicator: 'yellow' });
			return;
		}

		// Use XMLHttpRequest for binary download with proper error handling
		// 报表模式:POST export_report_excel(筛选不落 GET URL)
		if (this.is_report_mode) {
			await this._report_post_download(
				'zhiz_print.api.report_print.export_report_excel',
				{
					report_name: this.report_name,
					filters: this.report_filters || {},
					design_name: this.current_design,
					params: this.current_params || {},
				},
				this.report_name + '-' + (this.current_design_info?.design_name || 'report') + '.xlsx'
			);
			frappe.show_alert({ message: __('Excel exported'), indicator: 'green' });
			this.record_export_log('Export Excel');
			return;
		}

		const excelParams = new URLSearchParams({
			doctype: this.frm.doctype,
			docname: this.frm.docname,
			design_name: this.current_design,
			params: JSON.stringify(this.current_params || {})
		});
		const url = '/api/method/zhiz_print.api.print_designer.export_print_excel?' + excelParams;

		frappe.show_alert({ message: __('Exporting Excel...'), indicator: 'blue' });

		try {
			const blob = await new Promise((resolve, reject) => {
				const xhr = new XMLHttpRequest();
				xhr.open('GET', url, true);
				xhr.responseType = 'blob';
				xhr.setRequestHeader('X-Frappe-CSRF-Token', frappe.csrf_token);
				xhr.onload = function() {
					if (xhr.status === 200) {
						const contentType = xhr.getResponseHeader('Content-Type') || '';
						if (contentType.indexOf('application/json') !== -1 || contentType.indexOf('text/html') !== -1) {
							// Server returned JSON error instead of binary
							const reader = new FileReader();
							reader.onload = function() {
								try {
									const err = JSON.parse(reader.result);
									reject(new Error(err.exception || err.message || __('Export failed')));
								} catch (e) {
									reject(new Error(reader.result || __('Export failed')));
								}
							};
							reader.readAsText(xhr.response);
						} else {
							resolve(xhr.response);
						}
					} else {
						reject(new Error(__('Export failed (HTTP {0})', [xhr.status])));
					}
				};
				xhr.onerror = function() {
					reject(new Error(__('Network error')));
				};
				xhr.send();
			});

			// Extract filename from Content-Disposition header or use default
			const filename = this.frm.docname + '-' + (this.current_design_info?.design_name || 'export') + '.xlsx';

			// Trigger browser download
			const blobUrl = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = blobUrl;
			a.download = filename;
			document.body.appendChild(a);
			a.click();
			document.body.removeChild(a);
			URL.revokeObjectURL(blobUrl);

			frappe.show_alert({ message: __('Excel exported'), indicator: 'green' });
			this.record_export_log('Export Excel');
		} catch (e) {
			console.error('Excel export failed:', e);
			frappe.show_alert({ message: e.message || __('Excel export failed'), indicator: 'red' });
		}
	}

	async record_export_log(export_type) {
		try {
			await frappe.call({
				method: 'zhiz_print.api.print_designer.record_print_log',
				args: this._log_args(export_type)
			});
			this.load_print_logs();
		} catch (e) {
			console.error('Failed to record export log:', e);
		}
	}

	render_pdf() {
		if (this.is_super_print_mode) {
			this.generate_super_pdf();
		} else {
			super.render_pdf();
		}
	}

	escapeHtml(text) {
		if (!text) return '';
		const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
		return String(text).replace(/[&<>"']/g, m => map[m]);
	}

	// Original method
	selected_format() {
		if (this.is_super_print_mode) return 'Standard';
		let current_print_format = this.print_format_selector?.val();
		if (current_print_format) return current_print_format;
		let default_format = this.frm.meta.default_print_format || 'Standard';
		frappe.call({
			method: 'zhiz_print.utils.utils.get_print_format',
			args: { doc: this.frm.doc }
		}).then((r) => {
			this.print_format_selector?.val(r.message || default_format);
			this.refresh_print_format();
		});
		return default_format;
	}
};



// CSS for print page layout is loaded via app_include_css (print_designer.css)

