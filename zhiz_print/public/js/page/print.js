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
		let superPrintEnabled = false;
		if (pd && pd.enabled && doctype) {
			if (!pd.enable_mode || pd.enable_mode === 'Enable for All') {
				superPrintEnabled = true;
			} else if (pd.enable_mode === 'Enable for Specific') {
				const list = pd.enabled_doctypes || [];
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

		// Layout
		this.page.main.html(`
			<div class="super-print-layout">
				<div class="super-print-sidebar" id="super-print-sidebar">
					<div class="sp-sidebar-top">
						<div class="sp-sidebar-header">
							<i class="fa fa-print"></i> ${__('Print Templates')}
						</div>
						<div class="sp-sidebar-body" id="sp-template-list">
							<div class="sp-loading"><i class="fa fa-spinner fa-spin"></i> ${__('Loading...')}</div>
						</div>
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
	}

	// ==================== Toolbar Override ====================

	_is_preview_only() {
		// v15.04.25: draft_no_print is a per-design toggle that blocks printing
		// when the *document* is in draft state (docstatus=0). Submitted docs
		// print normally even when the design has draft_no_print=1.
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
			if (pd?.allow_export_pdf !== false) {
				this.page.add_button(__('Export PDF'), () => this.generate_super_pdf(), { icon: 'es-solid-pdf' });
			}
			if (pd?.allow_export_excel !== false) {
				this.page.add_button(__('Export Excel'), () => this.export_super_excel(), { icon: 'es-solid-excel' });
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
			const paper = this.current_design_info?.print_paper;
			if (paper) {
				frappe.set_route('Form', 'Super Print Paper', paper);
			} else {
				frappe.show_alert({ message: __('Please select a print template first'), indicator: 'yellow' });
			}
		});

		// Print Design → Open the currently selected print design document
		this.page.add_menu_item(__('Print Design'), () => {
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
			const res = await frappe.call({
				method: 'zhiz_print.api.print_designer.get_available_designs',
				args: { doctype: this.frm.doctype, docname: this.frm.docname }
			});

			const designs = res.message || [];
			if (designs.length === 0) {
				listEl.innerHTML = `
					<div class="sp-empty">
						<p>${__('No print templates available')}</p>
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
		btn.innerHTML = '<i class="fa fa-plus"></i> ' + __('New Print Design');
		btn.addEventListener('click', () => {
			const hash = Math.random().toString(36).substring(2, 12);
			frappe.route_options = { target_doctype: this.frm.doctype };
			frappe.set_route('Form', 'Super Print Design', 'new-super-print-design-' + hash);
		});
		listEl.appendChild(btn);
	}

	async on_template_click(design, el) {
		// Highlight selected
		document.querySelectorAll('.sp-template-item').forEach(i => i.classList.remove('active'));
		el.classList.add('active');

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

	// ==================== Preview Rendering ====================

	async render_preview() {
		if (!this.current_design) return;

		const area = document.getElementById('sp-preview-area');

		area.innerHTML = '<div class="sp-loading"><i class="fa fa-spinner fa-spin fa-2x" style="color:#2196f3"></i><p class="text-muted" style="margin-top:10px">' + __('Rendering preview...') + '</p></div>';

		try {
			const result = await frappe.call({
				method: 'zhiz_print.api.print_designer.render_print_preview',
				args: {
					doctype: this.frm.doctype,
					docname: this.frm.docname,
					design_name: this.current_design,
					params: this.current_params || {},
				}
			});

			if (result.message) {
				const { html, paper_width, paper_height, margin_top, margin_bottom, margin_left, margin_right } = result.message;
				this.current_preview_html = html;

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

				// Parse .print-page elements in HTML and render page by page
				const parser = new DOMParser();
				const parsed = parser.parseFromString(html, 'text/html');
				const printPages = parsed.querySelectorAll('.print-page');

				area.innerHTML = '';
				const pagesContainer = document.createElement('div');
				pagesContainer.className = 'sp-pages-container';
				pagesContainer.style.cssText = 'transform:scale(' + finalScale + ');transform-origin:top center;display:flex;flex-direction:column;align-items:center;gap:20px;padding-bottom:20px;';

				// Build all page structures first, mount to DOM, then write iframe content
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

						// Margin dashed line
						const marginLine = document.createElement('div');
						marginLine.className = 'sp-margin-line';
						marginLine.style.cssText =
							'position:absolute;' +
							'top:' + mTop + 'px;left:' + mLeft + 'px;' +
							'right:' + mRight + 'px;bottom:' + mBottom + 'px;' +
							'border:1px dashed rgba(0,120,215,0.4);' +
							'pointer-events:none;z-index:10;';
						pageWrapper.appendChild(marginLine);

						// Page content iframe
						const iframe = document.createElement('iframe');
						iframe.className = 'sp-iframe';
						iframe.style.cssText = 'width:100%;height:100%;border:none;overflow:hidden;';
			iframe.scrolling = 'no';
						pageWrapper.appendChild(iframe);

						pagesContainer.appendChild(pageWrapper);
						pageWrappers.push({ iframe, pageEl });
					});
				} else {
					// No page-break markers, treat as single page
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

				// Mount to DOM first so iframe can access contentDocument
				area.appendChild(pagesContainer);

				// Wait for browser to initialize iframes before writing content
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
		} catch (e) {
			console.error('Preview render failed:', e);
			area.innerHTML = '<div class="alert alert-danger" style="margin:20px"><i class="fa fa-exclamation-circle"></i> Preview render failed: ' + this.escapeHtml(e.message || String(e)) + '</div>';
		}
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
				args: { doctype: this.frm.doctype, docname: this.frm.docname }
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

	printit() {
		if (this.is_super_print_mode) {
			this.super_printit();
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
				args: {
					doctype: this.frm.doctype,
					docname: this.frm.docname,
					design_name: this.current_design,
					params: this.current_params || {},
					preview_html: this.current_preview_html,
					export_type: 'Print'
				}
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
		let printHtml = this.current_preview_html;

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

		const params = new URLSearchParams({
			doctype: this.frm.doctype,
			docname: this.frm.docname,
			design_name: this.current_design,
			params: JSON.stringify(this.current_params || {}),
		});
		const url = '/api/method/zhiz_print.api.print_designer.generate_print_pdf?' + params;
		const w = window.open(url, '_blank');
		if (!w) {
			frappe.msgprint(__('Please allow pop-up windows'));
			return;
		}

		// Record PDF export log
		this.record_export_log('Export PDF');
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
				args: {
					doctype: this.frm.doctype,
					docname: this.frm.docname,
					design_name: this.current_design || '',
					params: this.current_params || {},
					preview_html: this.current_preview_html || '',
					export_type: export_type
				}
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

