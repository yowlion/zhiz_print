// Super Print Page - 超级打印页面
// 启用时：左侧模板选择 + 中间预览
// 未启用时：保持原有 Frappe 打印逻辑


frappe.ui.form.PrintView = class SuperPrintView extends frappe.ui.form.PrintView {

	// ==================== make() 拦截 ====================
	// 在构造函数中 make() 最先执行，此时通过路由判断是否启用超级打印
	// 如果启用则跳过原始 UI 创建，避免旧界面闪烁

	make() {
		// 从路由获取当前 doctype
		const route = frappe.get_route();
		const doctype = route[1];
		const pd = frappe.boot.zhiz_print?.print_designer;
		let superPrintEnabled = false;
		if (pd && pd.enabled && doctype) {
			if (!pd.enable_mode || pd.enable_mode === '全部单据启用') {
				superPrintEnabled = true;
			} else if (pd.enable_mode === '指定单据启用') {
				const list = pd.enabled_doctypes || [];
				superPrintEnabled = list.includes(doctype);
			}
		}

		if (superPrintEnabled) {
			this.is_super_print_mode = true;
			// 仅做最小初始化，跳过原始 UI 创建
			this.wrapper = $(this.wrapper || []);
			this.print_settings = frappe.model.get_doc(":Print Settings", "Print Settings");
			return;
		}

		// 非超级打印模式，走原始 make()
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

		// 原有逻辑
		this.is_super_print_mode = false;
		this.page && this.page.sidebar && this.page.sidebar.empty();
		this.setup_sidebar();
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

// ==================== 超级打印模式 ====================

	async setup_super_print_page() {
		// 隐藏 sidebar，清空主区域
		if (this.page.sidebar) this.page.sidebar.hide();
		this.page.main.empty();

		// 重写工具栏和菜单
		this.setup_toolbar();
		this.setup_menu();

		// 整体布局
		this.page.main.html(`
			<div class="super-print-layout">
				<div class="super-print-sidebar" id="super-print-sidebar">
					<div class="sp-sidebar-top">
						<div class="sp-sidebar-header">
							<i class="fa fa-print"></i> 打印模板
						</div>
						<div class="sp-sidebar-body" id="sp-template-list">
							<div class="sp-loading"><i class="fa fa-spinner fa-spin"></i> 加载中...</div>
						</div>
					</div>
					<div class="sp-sidebar-bottom">
						<div class="sp-sidebar-header" id="sp-log-header">
							<i class="fa fa-history"></i> 已打印 <span id="sp-log-count">0</span> 次
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
							<p class="text-muted">请从左侧选择打印模板</p>
						</div>
					</div>
				</div>
			</div>
		`);

		// 加载模板列表
		await this.load_templates();
	}

	// ==================== 工具栏重写 ====================

	setup_toolbar() {
		if (!this.is_super_print_mode) {
			super.setup_toolbar();
			return;
		}

		// 清除所有默认按钮
		this.page.clear_primary_action();
		this.page.clear_custom_actions();
		this.page.clear_actions();
		this.page.clear_icons();
		$(this.page.inner_toolbar).find('.inner-page-message').remove();

		// 仅保留：打印主按钮 + 导出按钮
		this.page.set_primary_action(__('Print'), () => this.printit(), 'printer');
		this.page.add_button(__('导出PDF'), () => this.generate_super_pdf(), { icon: 'es-solid-pdf' });
		this.page.add_button(__('导出Excel'), () => this.export_super_excel(), { icon: 'es-solid-excel' });
	}

	// ==================== 菜单重写 ====================

	setup_menu() {
		if (!this.is_super_print_mode) {
			super.setup_menu();
			return;
		}

		this.page.clear_menu();

		// 纸张设置 → 打开当前模板关联的纸张文档
		this.page.add_menu_item(__('纸张设置'), () => {
			const paper = this.current_design_info?.print_paper;
			if (paper) {
				frappe.set_route('Form', 'Super Print Paper', paper);
			} else {
				frappe.show_alert({ message: '请先选择打印模板', indicator: 'yellow' });
			}
		});

		// 打印设计 → 打开当前选中的打印设计文档
		this.page.add_menu_item(__('打印设计'), () => {
			if (this.current_design) {
				frappe.set_route('Form', 'Super Print Design', this.current_design);
			} else {
				frappe.show_alert({ message: '请先选择打印模板', indicator: 'yellow' });
			}
		});
	}

	// ==================== 模板列表 ====================

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
						<p>暂无打印模板</p>
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

			// 有模板也在底部显示新建按钮
			this._append_new_design_btn(listEl);

			// 加载打印日志
			this.load_print_logs();

			// 自动选择第一个模板
			const firstItem = listEl.querySelector('.sp-template-item');
			if (firstItem) {
				this.on_template_click(designs[0], firstItem);
			}

		} catch (e) {
			console.error('加载打印模板失败:', e);
			listEl.innerHTML = `<div class="sp-error"><i class="fa fa-exclamation-circle"></i> 加载失败</div>`;
		}
	}

	_append_new_design_btn(listEl) {
		const btn = document.createElement('div');
		btn.className = 'sp-new-design-btn';
		btn.innerHTML = '<i class="fa fa-plus"></i> 新建打印设计';
		btn.addEventListener('click', () => {
			const hash = Math.random().toString(36).substring(2, 12);
			frappe.route_options = { target_doctype: this.frm.doctype };
			frappe.set_route('Form', 'Super Print Design', 'new-super-print-design-' + hash);
		});
		listEl.appendChild(btn);
	}

	async on_template_click(design, el) {
		// 高亮选中
		document.querySelectorAll('.sp-template-item').forEach(i => i.classList.remove('active'));
		el.classList.add('active');

		this.current_design = design.name;
		this.current_design_info = design;

		// 参数弹窗
		if (design.has_parameters && design.parameters?.length > 0) {
			const params = await this.show_parameter_dialog(design);
			if (params === null) return;
			this.current_params = params;
		} else {
			this.current_params = {};
		}

		// 渲染预览
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
				title: `打印参数 - ${design.design_name}`,
				fields: fields,
				primary_action_label: '确定',
				primary_action: (values) => {
					resolved = true;
					dialog.hide();
					resolve(values);
				}
			});

			dialog.get_secondary_btn().show();
			dialog.set_secondary_action_label('取消');
			dialog.set_secondary_action(() => {
				dialog.hide();
			});

			dialog.onhide = () => {
				if (!resolved) resolve(null);
			};
			dialog.show();
		});
	}

	// ==================== 预览渲染 ====================

	async render_preview() {
		if (!this.current_design) return;

		const area = document.getElementById('sp-preview-area');

		area.innerHTML = '<div class="sp-loading"><i class="fa fa-spinner fa-spin fa-2x" style="color:#2196f3"></i><p class="text-muted" style="margin-top:10px">正在渲染预览...</p></div>';

		try {
			const result = await frappe.call({
				method: 'zhiz_print.api.print_designer.render_print_preview',
				args: {
					doctype: this.frm.doctype,
					docname: this.frm.docname,
					design_name: this.current_design,
					params: this.current_params || {}
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

				// 解析 HTML 中的 .print-page 元素，逐页渲染
				const parser = new DOMParser();
				const parsed = parser.parseFromString(html, 'text/html');
				const printPages = parsed.querySelectorAll('.print-page');

				area.innerHTML = '';
				const pagesContainer = document.createElement('div');
				pagesContainer.className = 'sp-pages-container';
				pagesContainer.style.cssText = 'transform:scale(' + scale + ');transform-origin:top center;display:flex;flex-direction:column;align-items:center;gap:20px;padding-bottom:20px;';

				// 先构建所有页面结构，再统一挂载到 DOM，最后写入 iframe 内容
				const pageWrappers = [];

				if (printPages.length > 0) {
					// 多页模式：每页独立纸张容器
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

						// 边距虚线
						const marginLine = document.createElement('div');
						marginLine.className = 'sp-margin-line';
						marginLine.style.cssText =
							'position:absolute;' +
							'top:' + mTop + 'px;left:' + mLeft + 'px;' +
							'right:' + mRight + 'px;bottom:' + mBottom + 'px;' +
							'border:1px dashed rgba(0,120,215,0.4);' +
							'pointer-events:none;z-index:10;';
						pageWrapper.appendChild(marginLine);

						// 页面内容 iframe
						const iframe = document.createElement('iframe');
						iframe.className = 'sp-iframe';
						iframe.style.cssText = 'width:100%;height:100%;border:none;';
						pageWrapper.appendChild(iframe);

						pagesContainer.appendChild(pageWrapper);
						pageWrappers.push({ iframe, pageEl });
					});
				} else {
					// 无分页标记，按单页处理
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

				// 先挂载到 DOM，iframe 才能访问 contentDocument
				area.appendChild(pagesContainer);

				// 写入各页 iframe 内容
				pageWrappers.forEach(({ iframe, pageEl }) => {
					const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
					iframeDoc.open();
					iframeDoc.write(pageEl
						? this._build_single_page_html(pageEl, parsed, previewW, previewH)
						: html);
					iframeDoc.close();
				});
			}
		} catch (e) {
			console.error('渲染预览失败:', e);
			area.innerHTML = '<div class="alert alert-danger" style="margin:20px"><i class="fa fa-exclamation-circle"></i> 预览渲染失败: ' + this.escapeHtml(e.message || String(e)) + '</div>';
		}
	}

	_build_single_page_html(pageEl, fullDoc, previewW, previewH) {
		// 提取原始 <style> 标签
		const styles = fullDoc.querySelectorAll('style');
		let styleHtml = '';
		styles.forEach(s => { styleHtml += s.outerHTML; });

		return '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n' +
			styleHtml +
			'\n<style>\n@page { size: ' + (previewW / 4) + 'mm ' + (previewH / 4) + 'mm; margin: 0; }\n' +
			'* { box-sizing: border-box; }\nbody { margin: 0; padding: 0; }\n</style>\n' +
			'</head>\n<body>\n' + pageEl.outerHTML + '\n</body>\n</html>';
	}

	// ==================== 打印日志 ====================

	async load_print_logs() {
		try {
			const res = await frappe.call({
				method: 'zhiz_print.api.print_designer.get_print_log_list',
				args: { doctype: this.frm.doctype, docname: this.frm.docname }
			});

			const data = res.message || {};
			const logs = data.logs || [];
			const totalCount = data.total_count || 0;

			// 更新计数
			const countEl = document.getElementById('sp-log-count');
			if (countEl) countEl.textContent = totalCount;

			// 更新日志列表
			const listEl = document.getElementById('sp-log-list');
			if (!listEl) return;

			if (logs.length === 0) {
				listEl.innerHTML = '<div class="sp-empty-log">暂无打印记录</div>';
				return;
			}

			listEl.innerHTML = '';
			logs.forEach(log => {
				const item = document.createElement('div');
				item.className = 'sp-log-item';

				const time = log.print_time ? frappe.datetime.str_to_user(log.print_time) : '';
				const userFullName = log.print_user ? log.print_user : '';
				const userImg = log.print_user ? frappe.avatar(log.print_user) : '';

				// 根据导出类型选择图标和颜色
				const exportType = (log.export_type || '打印').trim();
				let typeIcon = 'fa-print';
				let typeColor = '#2196f3';
				if (exportType === '导出PDF') {
					typeIcon = 'fa-file-pdf-o';
					typeColor = '#e53935';
				} else if (exportType === '导出Excel') {
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
			console.error('加载打印日志失败:', e);
		}
	}

	// ==================== 打印/PDF/Excel ====================

	printit() {
		if (this.is_super_print_mode) {
			this.super_printit();
		} else {
			super.printit();
		}
	}

	async super_printit() {
		if (!this.current_preview_html) {
			frappe.show_alert({ message: '请先选择打印模板', indicator: 'yellow' });
			return;
		}

		// 记录打印日志（含预览HTML快照）
		try {
			await frappe.call({
				method: 'zhiz_print.api.print_designer.record_print_log',
				args: {
					doctype: this.frm.doctype,
					docname: this.frm.docname,
					design_name: this.current_design,
					params: this.current_params || {},
					preview_html: this.current_preview_html,
					export_type: '打印'
				}
			});
		} catch (e) {
			console.error('记录打印日志失败:', e);
		}

		// 使用隐藏 iframe 直接打印（不打开新窗口）
		const printFrame = document.createElement('iframe');
		printFrame.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:0;height:0;border:none;';
		document.body.appendChild(printFrame);

		const frameDoc = printFrame.contentDocument || printFrame.contentWindow.document;
		frameDoc.open();
		frameDoc.write(this.current_preview_html);
		frameDoc.close();

		// 等待内容加载后调用打印
		printFrame.onload = () => {
			setTimeout(() => {
				printFrame.contentWindow.print();
				// 打印对话框关闭后移除 iframe
				setTimeout(() => {
					document.body.removeChild(printFrame);
				}, 1000);
			}, 300);
		};

		this.load_print_logs();
	}

	async generate_super_pdf() {
		if (!this.current_design) {
			frappe.show_alert({ message: '请先选择打印模板', indicator: 'yellow' });
			return;
		}

		// 根据 Super Setting 中的 pdf_engine_mode 选择引擎
		const engine_mode = await frappe.db.get_single_value('Zprint Setting', 'pdf_engine_mode') || 'wkhtmltopdf';
		const engine_map = {
			'WeasyPrint': 'generate_print_pdf',
			'wkhtmltopdf': 'generate_print_pdf_wkhtmltopdf',
			'Chromium': 'generate_print_pdf_chromium',
		};
		const method = engine_map[engine_mode] || 'generate_print_pdf_wkhtmltopdf';
		const params = new URLSearchParams({
			doctype: this.frm.doctype,
			docname: this.frm.docname,
			design_name: this.current_design,
			params: JSON.stringify(this.current_params || {})
		});
		const url = '/api/method/zhiz_print.api.print_designer.' + method + '?' + params;
		const w = window.open(url, '_blank');
		if (!w) {
			frappe.msgprint(__('请允许弹出窗口'));
			return;
		}

		// 记录导出PDF日志
		this.record_export_log('导出PDF');
	}


	async export_super_excel() {
		if (!this.current_design) {
			frappe.show_alert({ message: '请先选择打印模板', indicator: 'yellow' });
			return;
		}

		try {
			const result = await frappe.call({
				method: 'zhiz_print.api.print_designer.export_print_excel',
				args: {
					doctype: this.frm.doctype,
					docname: this.frm.docname,
					design_name: this.current_design,
					params: this.current_params || {}
				}
			});

			if (result.message?.xlsx_base64) {
				const raw = atob(result.message.xlsx_base64);
				const uInt8 = new Uint8Array(raw.length);
				for (let i = 0; i < raw.length; i++) uInt8[i] = raw.charCodeAt(i);
				const blob = new Blob([uInt8], {
					type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
				});
				const url = URL.createObjectURL(blob);
				const a = document.createElement('a');
				a.href = url;
				a.download = result.message.filename || `${this.frm.docname}.xlsx`;
				a.click();
				setTimeout(() => URL.revokeObjectURL(url), 60000);
				frappe.show_alert({ message: 'Excel 已导出', indicator: 'green' });

				// 记录导出Excel日志
				this.record_export_log('导出Excel');
			}
		} catch (e) {
			console.error('Excel 导出失败:', e);
			frappe.show_alert({ message: `Excel 导出失败: ${e.message || String(e)}`, indicator: 'red' });
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
			console.error('记录导出日志失败:', e);
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

	// 原有方法
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



// 注入超级打印页面 CSS（一次性）
(function injectSuperPrintCSS() {
	if (document.getElementById('super-print-css')) return;
	const style = document.createElement('style');
	style.id = 'super-print-css';
	style.innerHTML = `
		/* 整体布局 */
		.super-print-layout { display:flex; height:calc(100vh - 120px); margin:-15px; }
		.super-print-sidebar { width:200px; min-width:200px; background:#fff; border-right:1px solid #e0e0e0; display:flex; flex-direction:column; }
		.super-print-main { flex:1; display:flex; flex-direction:column; overflow:hidden; background:#f5f5f5; }

		/* 侧边栏上下两栏 */
		.sp-sidebar-top { flex:1; display:flex; flex-direction:column; min-height:0; border-bottom:1px solid #e0e0e0; }
		.sp-sidebar-bottom { flex:1; display:flex; flex-direction:column; min-height:0; }
		.sp-sidebar-header { padding:10px 16px; font-size:12px; font-weight:600; color:#333; border-bottom:1px solid #eee; flex-shrink:0; }
		.sp-sidebar-header i { color:#2196f3; margin-right:4px; }
		.sp-sidebar-body { flex:1; overflow-y:auto; padding:4px 0; }

		/* 模板列表项 */
		.sp-template-item {
			display:flex; align-items:center; gap:8px; padding:9px 16px;
			cursor:pointer; font-size:12px; color:#555; transition:all .15s;
			border-left:3px solid transparent;
		}
		.sp-template-item:hover { background:#f0f7ff; color:#333; }
		.sp-template-item.active {
			background:#e3f2fd; color:#1565c0; font-weight:600;
			border-left-color:#2196f3;
		}
		.sp-template-item i { font-size:14px; color:#999; flex-shrink:0; }
		.sp-template-item.active i { color:#2196f3; }
		.sp-template-item span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

		/* 空状态 / 加载 / 错误 */
		.sp-loading, .sp-empty, .sp-error { text-align:center; padding:30px 16px; color:#999; font-size:12px; }
		.sp-loading i { margin-bottom:8px; }
		.sp-no-preview { display:flex; flex-direction:column; align-items:center; justify-content:center; height:100%; color:#ccc; }

		/* 预览区 */
		.sp-preview-area { flex:1; overflow:auto; padding:20px; display:flex; flex-direction:column; align-items:center; }

		/* 日志列表 */
		.sp-log-item { padding:8px 16px; cursor:pointer; border-left:3px solid transparent; transition:all .15s; }
		.sp-log-item:hover { background:#f0f7ff; }
		.sp-log-line1 { display:flex; align-items:center; gap:5px; font-size:11px; color:#333; }
		.sp-log-line2 { display:flex; align-items:center; gap:5px; font-size:11px; color:#666; margin-top:3px; }
		.sp-log-line2 .avatar { width:18px !important; height:18px !important; flex-shrink:0; }
		.sp-log-line3 { font-size:10px; color:#999; margin-top:2px; }
		.sp-log-line3 i { font-size:10px; }
		.sp-log-count { color:#2196f3; font-weight:600; font-size:10px; flex-shrink:0; }
			.sp-log-type-icon { font-size:10px; flex-shrink:0; }
		.sp-log-design { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; font-weight:500; }
		.sp-log-user { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
		.sp-empty-log { text-align:center; padding:20px 8px; color:#ccc; font-size:11px; }
			.sp-new-design-btn { display:flex; align-items:center; justify-content:center; gap:5px; margin:8px 12px; padding:7px 0; font-size:11px; color:#2196f3; background:#e3f2fd; border-radius:4px; cursor:pointer; transition:all .15s; }
			.sp-new-design-btn:hover { background:#bbdefb; }
	`;
	document.head.appendChild(style);
})();
