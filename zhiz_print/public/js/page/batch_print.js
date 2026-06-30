// Batch Print Page
// Renders multiple documents with a shared print template
// Layout: left sidebar (templates + logs) | right preview area (all docs consecutively)

frappe.pages["batch-print"].on_page_load = function (wrapper) {
    frappe.ui.make_app_page({ parent: wrapper });

    const page = wrapper.page;
    page.set_title(__('Batch Print'));

    // Read from URL query params (survives refresh) or route_options (from navigation)
    const urlParams = new URLSearchParams(window.location.search);
    let doctype = urlParams.get("doctype");
    let docnames = urlParams.get("docnames");

    if (!doctype || !docnames) {
        const routeOpts = frappe.route_options || {};
        doctype = doctype || routeOpts.doctype;
        docnames = docnames || routeOpts.docnames;
    }

    // Parse docnames: JSON array or comma-separated
    if (typeof docnames === "string") {
        try {
            docnames = JSON.parse(docnames);
        } catch (e) {
            docnames = docnames.split(",").map(s => s.trim()).filter(Boolean);
        }
    }

    if (!doctype || !docnames || !docnames.length) {
        page.main.html(
            '<div class="sp-batch-empty-state">' +
                '<i class="fa fa-print" style="font-size:48px;color:#ccc"></i>' +
                '<p class="text-muted" style="margin-top:15px">' +
                __("No documents selected for batch printing") +
                "</p>" +
                "</div>"
        );
        return;
    }

    const view = new zhiz_print.BatchPrintView(page, doctype, docnames);
    view.setup_page();
};

frappe.provide("zhiz_print");

zhiz_print.BatchPrintView = class BatchPrintView {
    constructor(page, doctype, docnames) {
        this.page = page;
        this.doctype = doctype;
        this.docnames = docnames;
        this.current_design = null;
        this.current_design_info = null;
        this.current_params = {};
        this.preview_results = [];
        this.preview_errors = [];
        this.preview_skipped = [];
    }

    async setup_page() {
        // Check license
        const lic = frappe.boot.zhiz_print?.print_designer;
        if (lic && lic.expired) {
            this.page.main.html(`
                <div class="sp-expired-notice" style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:calc(100vh - 120px);color:#e74c3c;">
                    <i class="fa fa-lock" style="font-size:48px;margin-bottom:20px"></i>
                    <h3>${__("Subscription Expired")}</h3>
                    <p class="text-muted" style="margin-top:10px;max-width:400px;text-align:center">
                        ${lic.license_message || __("Your subscription has expired.")}
                    </p>
                    <a href="/app/zprint-setting" class="btn btn-primary" style="margin-top:20px">
                        ${__("Go to Settings")}
                    </a>
                </div>
            `);
            return;
        }

        // Hide sidebar
        if (this.page.sidebar) this.page.sidebar.hide();
        this.page.main.empty();

        this.setup_toolbar();

        // Build layout
        this.page.main.html(`
            <div class="super-print-layout">
                <div class="super-print-sidebar" id="super-print-sidebar">
                    <div class="sp-sidebar-top">
                        <div class="sp-sidebar-header">
                            <i class="fa fa-print"></i> ${__("Print Templates")}
                            <span class="sp-batch-doc-count">${this.docnames.length} ${__("docs")}</span>
                        </div>
                        <div class="sp-sidebar-body" id="sp-template-list">
                            <div class="sp-loading"><i class="fa fa-spinner fa-spin"></i> ${__("Loading...")}</div>
                        </div>
                    </div>
                    <div class="sp-sidebar-bottom">
                        <div class="sp-sidebar-header" id="sp-log-header">
                            <i class="fa fa-history"></i> ${__("Batch Logs")} <span id="sp-log-count">0</span> ${__("records")}
                        </div>
                        <div class="sp-sidebar-body sp-log-list" id="sp-log-list">
                            <div class="sp-loading"><i class="fa fa-spinner fa-spin"></i></div>
                        </div>
                    </div>
                </div>
                <div class="super-print-main">
                    <div class="sp-batch-info-bar" id="sp-batch-info-bar">
                        <span class="sp-batch-info-doctype">${this.esc(this.doctype)}</span>
                        <span class="sp-batch-info-sep">/</span>
                        <span class="sp-batch-info-count">${this.docnames.length} ${__("documents selected")}</span>
                    </div>
                    <div class="sp-preview-area" id="sp-preview-area">
                        <div class="sp-no-preview">
                            <i class="fa fa-print" style="font-size:48px;color:#ccc"></i>
                            <p class="text-muted">${__("Please select a print template from the left")}</p>
                        </div>
                    </div>
                </div>
            </div>
        `);

        await this.load_templates();
    }

    setup_toolbar() {
        this.page.clear_primary_action();
        this.page.clear_custom_actions();
        this.page.clear_actions();
        this.page.clear_icons();
        $(this.page.inner_toolbar).find(".inner-page-message").remove();

        this.page.set_primary_action(__("Print All"), () => this.print_all(), "printer");
        const pd = frappe.boot.zhiz_print?.print_designer;
        if (pd?.allow_export_pdf !== false) {
            this.page.add_button(__("Export All PDF"), () => this.export_all_pdf(), {
                icon: "es-solid-pdf",
            });
        }
        if (pd?.allow_export_excel !== false) {
            this.page.add_button(__("Export All Excel"), () => this.export_all_excel(), {
                icon: "es-solid-excel",
            });
        }

        // Back button
        this.page.add_menu_item(__("Back to List"), () => {
            frappe.set_route("List", this.doctype);
        });

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

    // ==================== Template List ====================

    async load_templates() {
        const listEl = document.getElementById("sp-template-list");
        try {
            const res = await frappe.call({
                method: "zhiz_print.api.batch_print.check_batch_print_enabled",
                args: { doctype: this.doctype },
            });

            const designs = res.message?.designs || [];
            if (designs.length === 0) {
                listEl.innerHTML = `<div class="sp-empty"><p>${__("No print templates available")}</p></div>`;
                return;
            }

            this.available_designs = designs;
            listEl.innerHTML = "";

            // Auto Match option at top
            const autoItem = document.createElement("div");
            autoItem.className = "sp-template-item sp-auto-match-item";
            autoItem.dataset.autoMatch = "1";
            autoItem.innerHTML = `<i class="fa fa-magic"></i><span>${__("Auto Match")}</span>`;
            autoItem.addEventListener("click", () => this.on_auto_match_click(autoItem));
            listEl.appendChild(autoItem);

            designs.forEach((d) => {
                const item = document.createElement("div");
                item.className = "sp-template-item";
                item.dataset.name = d.name;
                item.innerHTML = `<i class="fa fa-file-text-o"></i><span>${this.esc(d.design_name)}</span><span class="sp-template-priority">P${d.priority || 5}</span>`;
                item.addEventListener("click", () => this.on_template_click(d, item));
                listEl.appendChild(item);
            });

            // Load batch logs
            this.load_batch_logs();

            // Auto-select Auto Match
            this.on_auto_match_click(autoItem);
        } catch (e) {
            console.error("Failed to load templates:", e);
            listEl.innerHTML = `<div class="sp-error"><i class="fa fa-exclamation-circle"></i> ${__("Loading failed")}</div>`;
        }
    }

    on_auto_match_click(el) {
        document.querySelectorAll(".sp-template-item").forEach((i) => i.classList.remove("active"));
        el.classList.add("active");

        this.current_design = null;
        this.current_design_info = null;
        this.auto_match = true;

        this.render_batch_preview();
    }

    async on_template_click(design, el) {
        document.querySelectorAll(".sp-template-item").forEach((i) => i.classList.remove("active"));
        el.classList.add("active");

        this.current_design = design.name;
        this.current_design_info = design;
        this.auto_match = false;

        // Parameter dialog
        if (design.has_parameters && design.parameters?.length > 0) {
            const params = await this.show_parameter_dialog(design);
            if (params === null) return;
            this.current_params = params;
        } else {
            this.current_params = {};
        }

        await this.render_batch_preview();
    }

    show_parameter_dialog(design) {
        return new Promise((resolve) => {
            let resolved = false;
            const fields = (design.parameters || []).map((p) => ({
                fieldname: p.param_name,
                label: p.param_label || p.param_name,
                fieldtype: p.param_type || "Data",
                default: p.default_value,
                reqd: p.reqd,
                options: p.options,
            }));

            const dialog = new frappe.ui.Dialog({
                title: __("Print Parameters") + " - " + design.design_name,
                fields: fields,
                primary_action_label: "OK",
                primary_action: (values) => {
                    resolved = true;
                    dialog.hide();
                    resolve(values);
                },
            });

            dialog.get_secondary_btn().show();
            dialog.set_secondary_action_label(__("Cancel"));
            dialog.set_secondary_action(() => {
                dialog.hide();
            });

            dialog.onhide = () => {
                if (!resolved) resolve(null);
            };
            dialog.show();
        });
    }

    // ==================== Batch Preview ====================

    async render_batch_preview() {
        if (!this.current_design && !this.auto_match) return;

        const area = document.getElementById("sp-preview-area");
        area.innerHTML =
            '<div class="sp-loading"><i class="fa fa-spinner fa-spin fa-2x" style="color:#2196f3"></i>' +
            '<p class="text-muted" style="margin-top:10px">' +
            __("Rendering batch preview...") +
            "</p></div>";

        try {
            const args = {
                doctype: this.doctype,
                docnames: JSON.stringify(this.docnames),
                params: this.current_params,
            };
            if (this.auto_match) {
                args.auto_match = 1;
            } else {
                args.design_name = this.current_design;
            }

            const result = await frappe.call({
                method: "zhiz_print.api.batch_print.batch_render_preview",
                args: args,
            });

            if (!result.message) {
                area.innerHTML = '<div class="alert alert-danger">' + __("Rendering failed") + "</div>";
                return;
            }

            this.preview_results = result.message.results || [];
            this.preview_errors = result.message.errors || [];
            this.preview_skipped = result.message.skipped || [];

            // Reflect skipped/error counts in the top info bar so the user sees the
            // policy-applied summary right away, not only at the bottom of the preview.
            this._update_info_bar();

            if (this.preview_results.length === 0) {
                area.innerHTML =
                    '<div class="alert alert-danger"><i class="fa fa-exclamation-circle"></i> ' +
                    __("All documents failed to render") +
                    "</div>";
                return;
            }

            // Use first result's paper dimensions for scaling
            const first = this.preview_results[0];
            const PX_PER_MM = 4;
            const previewW = (first.paper_width || 210) * PX_PER_MM;
            const previewH = (first.paper_height || 297) * PX_PER_MM;
            const mTop = (first.margin_top || 0) * PX_PER_MM;
            const mBottom = (first.margin_bottom || 0) * PX_PER_MM;
            const mLeft = (first.margin_left || 0) * PX_PER_MM;
            const mRight = (first.margin_right || 0) * PX_PER_MM;
            const containerWidth = area.offsetWidth - 40;
            const scale = Math.min(1, containerWidth / previewW);
            this.base_scale = scale;
            if (!this.user_zoom) this.user_zoom = 100;
            const finalScale = scale * (this.user_zoom / 100);

            area.innerHTML = "";
            const pagesContainer = document.createElement("div");
            pagesContainer.className = "sp-pages-container";
            pagesContainer.style.cssText =
                "transform:scale(" + finalScale + ");transform-origin:top center;display:flex;flex-direction:column;align-items:center;gap:8px;padding-bottom:20px;";

            const pageWrappers = [];

            this.preview_results.forEach((docResult, docIdx) => {
                // Document separator
                const separator = document.createElement("div");
                separator.className = "sp-doc-separator";
                let sepText = this.esc(docResult.docname) + " (" + (docIdx + 1) + "/" + this.preview_results.length + ")";
                if (this.auto_match && docResult.design_label) {
                    sepText += " - " + this.esc(docResult.design_label);
                }
                separator.textContent = sepText;
                pagesContainer.appendChild(separator);

                // Parse pages from HTML
                const parser = new DOMParser();
                const parsed = parser.parseFromString(docResult.html, "text/html");
                const printPages = parsed.querySelectorAll(".print-page");

                if (printPages.length > 0) {
                    printPages.forEach((pageEl) => {
                        const pageWrapper = document.createElement("div");
                        pageWrapper.className = "sp-paper-wrapper";
                        pageWrapper.style.cssText =
                            "width:" + previewW + "px;" +
                            "height:" + previewH + "px;" +
                            "background:white;" +
                            "box-shadow:0 2px 16px rgba(0,0,0,.12);" +
                            "position:relative;" +
                            "overflow:hidden;" +
                            "flex-shrink:0;";

                        const marginLine = document.createElement("div");
                        marginLine.className = "sp-margin-line";
                        marginLine.style.cssText =
                            "position:absolute;" +
                            "top:" + mTop + "px;left:" + mLeft + "px;" +
                            "right:" + mRight + "px;bottom:" + mBottom + "px;" +
                            "border:1px dashed rgba(0,120,215,0.4);" +
                            "pointer-events:none;z-index:10;";
                        pageWrapper.appendChild(marginLine);

                        const iframe = document.createElement("iframe");
                        iframe.className = "sp-iframe";
                        iframe.style.cssText = "width:100%;height:100%;border:none;overflow:hidden;";
			iframe.scrolling = 'no';
                        pageWrapper.appendChild(iframe);

                        pagesContainer.appendChild(pageWrapper);
                        pageWrappers.push({ iframe, pageEl, parsed, previewW, previewH });
                    });
                } else {
                    const pageWrapper = document.createElement("div");
                    pageWrapper.className = "sp-paper-wrapper";
                    pageWrapper.style.cssText =
                        "width:" + previewW + "px;" +
                        "min-height:" + previewH + "px;" +
                        "background:white;" +
                        "box-shadow:0 2px 16px rgba(0,0,0,.12);" +
                        "position:relative;";

                    const marginLine = document.createElement("div");
                    marginLine.className = "sp-margin-line";
                    marginLine.style.cssText =
                        "position:absolute;" +
                        "top:" + mTop + "px;left:" + mLeft + "px;" +
                        "right:" + mRight + "px;bottom:" + mBottom + "px;" +
                        "border:1px dashed rgba(0,120,215,0.4);" +
                        "pointer-events:none;z-index:10;";
                    pageWrapper.appendChild(marginLine);

                    const iframe = document.createElement("iframe");
                    iframe.className = "sp-iframe";
                    iframe.style.cssText = "width:100%;height:" + previewH + "px;border:none;";
                    pageWrapper.appendChild(iframe);
                    pagesContainer.appendChild(pageWrapper);
                    pageWrappers.push({ iframe, pageEl: null, parsed, previewW, previewH, fullHtml: docResult.html });
                }
            });

            // Show errors at bottom
            if (this.preview_errors.length > 0) {
                const errBox = document.createElement("div");
                errBox.className = "sp-batch-error-summary";
                errBox.innerHTML =
                    '<i class="fa fa-exclamation-triangle" style="color:#e53935"></i> ' +
                    this.esc(String(this.preview_errors.length)) + " " + __("documents failed to render") +
                    "<ul>" +
                    this.preview_errors.map((e) => "<li>" + this.esc(e.docname) + ": " + this.esc(e.error) + "</li>").join("") +
                    "</ul>";
                pagesContainer.appendChild(errBox);
            }

            // Show skipped drafts at bottom (info, not error — policy-driven, not a failure)
            if (this.preview_skipped && this.preview_skipped.length > 0) {
                const skipBox = document.createElement("div");
                skipBox.className = "sp-batch-skipped-summary";
                skipBox.innerHTML =
                    '<i class="fa fa-info-circle" style="color:#1976d2"></i> ' +
                    this.esc(String(this.preview_skipped.length)) + " " +
                    __("document(s) skipped as draft (design disables draft printing)") +
                    "<ul>" +
                    this.preview_skipped.map((s) => {
                        let label = this.esc(s.docname);
                        if (s.design_label) label += " — " + this.esc(s.design_label);
                        return "<li>" + label + "</li>";
                    }).join("") +
                    "</ul>";
                pagesContainer.appendChild(skipBox);
            }

            area.appendChild(pagesContainer);

            // Write iframe content
            pageWrappers.forEach(({ iframe, pageEl, parsed, previewW, previewH, fullHtml }) => {
                const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
                iframeDoc.open();
                if (pageEl) {
                    iframeDoc.write(this._build_single_page_html(pageEl, parsed, previewW, previewH));
                } else {
                    iframeDoc.write(fullHtml);
                }
                iframeDoc.close();
            });

            // Scroll to top
            area.scrollTop = 0;
        } catch (e) {
            console.error("Batch preview render failed:", e);
            area.innerHTML =
                '<div class="alert alert-danger" style="margin:20px"><i class="fa fa-exclamation-circle"></i> ' +
                this.esc(e.message || String(e)) +
                "</div>";
        }
    }

    _update_info_bar() {
        const bar = document.getElementById("sp-batch-info-bar");
        if (!bar) return;
        // Strip any previously appended status pills (keep only the original doctype/sep/count).
        bar.querySelectorAll(".sp-batch-info-status").forEach((n) => n.remove());
        const status = [];
        if (this.preview_results && this.preview_results.length > 0) {
            status.push(this.esc(String(this.preview_results.length)) + " " + __("to preview"));
        }
        if (this.preview_skipped && this.preview_skipped.length > 0) {
            status.push('<span style="color:#1976d2">' +
                this.esc(String(this.preview_skipped.length)) + " " + __("draft skipped") +
                "</span>");
        }
        if (this.preview_errors && this.preview_errors.length > 0) {
            status.push('<span style="color:#e53935">' +
                this.esc(String(this.preview_errors.length)) + " " + __("failed") +
                "</span>");
        }
        if (status.length === 0) return;
        const span = document.createElement("span");
        span.className = "sp-batch-info-status";
        span.innerHTML = '<span class="sp-batch-info-sep">/</span>' + status.join('<span class="sp-batch-info-sep">·</span>');
        bar.appendChild(span);
    }

    _build_single_page_html(pageEl, fullDoc, previewW, previewH) {
        const styles = fullDoc.querySelectorAll("style");
        let styleHtml = "";
        styles.forEach((s) => {
            styleHtml += s.outerHTML;
        });

        return (
            '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n' +
            styleHtml +
            '\n<style>\n@page { size: ' +
            previewW / 4 +
            "mm " +
            previewH / 4 +
            "mm; margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }\n" +
            "* { box-sizing: border-box; }\nbody { margin: 0; padding: 0; }\n</style>\n" +
            "</head>\n<body>\n" +
            pageEl.outerHTML +
            "\n</body>\n</html>"
        );
    }

    // ==================== Print All ====================

    async print_all() {
        if (!this.preview_results.length) {
            frappe.show_alert({ message: __("Please select a template and wait for preview"), indicator: "yellow" });
            return;
        }

        // Record batch print logs
        try {
            const logArgs = {
                doctype: this.doctype,
                docnames: JSON.stringify(this.docnames),
                design_name: this.current_design,
                params: this.current_params,
                export_type: "Print",
            };
            if (this.auto_match) {
                logArgs.auto_match = 1;
            }
            await frappe.call({
                method: "zhiz_print.api.batch_print.batch_record_print_log",
                args: logArgs,
            });
        } catch (e) {
            console.error("Failed to record batch logs:", e);
        }

        // Concatenate all HTML with page breaks between documents
        let allHtml = "";
        this.preview_results.forEach((docResult, idx) => {
            let docHtml = docResult.html;
            // Ensure page breaks between documents
            if (idx < this.preview_results.length - 1) {
                docHtml = docHtml.replace("</body>", '<div style="page-break-after:always"></div></body>');
            }
            // Strip outer html/head/body for concatenation
            const bodyMatch = docHtml.match(/<body[^>]*>([\s\S]*)<\/body>/i);
            const styleMatch = docHtml.match(/<style[^>]*>([\s\S]*?)<\/style>/gi);
            if (idx === 0) {
                // First doc: use full structure
                allHtml = docHtml;
            } else {
                // Append body content + styles
                if (styleMatch) {
                    allHtml = allHtml.replace("</head>", styleMatch.join("\n") + "\n</head>");
                }
                if (bodyMatch) {
                    allHtml = allHtml.replace("</body>", bodyMatch[1] + "\n</body>");
                }
            }
        });

        // Print via hidden iframe
        const printFrame = document.createElement("iframe");
        printFrame.style.cssText = "position:fixed;left:-9999px;top:-9999px;width:0;height:0;border:none;";
        document.body.appendChild(printFrame);

        // v15.04.39: 浏览器批量打印直接使用 HTML 原始 @page(paper 原始宽高),
        // 不再做 Force Landscape/Portrait 交换 — 多页打印方式已解决方向问题。

        const frameDoc = printFrame.contentDocument || printFrame.contentWindow.document;
        frameDoc.open();
        frameDoc.write(allHtml);
        frameDoc.close();

        printFrame.onload = () => {
            setTimeout(() => {
                printFrame.contentWindow.print();
                setTimeout(() => {
                    document.body.removeChild(printFrame);
                }, 1000);
            }, 300);
        };

        this.load_batch_logs();
    }

    // ==================== Export All PDF ====================

    async export_all_pdf() {
        if (!this.current_design && !this.auto_match) {
            frappe.show_alert({ message: __("Please select a template first"), indicator: "yellow" });
            return;
        }

        const pdfParams = {
            doctype: this.doctype,
            docnames: JSON.stringify(this.docnames),
            params: JSON.stringify(this.current_params || {}),
        };
        if (this.auto_match) {
            pdfParams.auto_match = 1;
        } else {
            pdfParams.design_name = this.current_design;
        }
        const params = new URLSearchParams(pdfParams);
        const url = "/api/method/zhiz_print.api.batch_print.batch_generate_pdf?" + params;
        const w = window.open(url, "_blank");
        if (!w) {
            frappe.msgprint(__("Please allow pop-up windows"));
            return;
        }

        // Record batch logs
        try {
            const logArgs = {
                doctype: this.doctype,
                docnames: JSON.stringify(this.docnames),
                design_name: this.current_design,
                params: this.current_params,
                export_type: "Export PDF",
            };
            if (this.auto_match) {
                logArgs.auto_match = 1;
            }
            await frappe.call({
                method: "zhiz_print.api.batch_print.batch_record_print_log",
                args: logArgs,
            });
            this.load_batch_logs();
        } catch (e) {
            console.error("Failed to record batch PDF logs:", e);
        }
    }

    // ==================== Export All Excel ====================

    async export_all_excel() {
        if (!this.current_design && !this.auto_match) {
            frappe.show_alert({ message: __("Please select a template first"), indicator: "yellow" });
            return;
        }

        const excelArgs = {
            doctype: this.doctype,
            docnames: JSON.stringify(this.docnames),
            params: JSON.stringify(this.current_params || {}),
        };
        if (this.auto_match) {
            excelArgs.auto_match = 1;
        } else {
            excelArgs.design_name = this.current_design;
        }
        const excelParams = new URLSearchParams(excelArgs);
        const url = "/api/method/zhiz_print.api.batch_print.batch_export_excel?" + excelParams;

        frappe.show_alert({ message: __("Exporting Excel..."), indicator: "blue" });

        try {
            const blob = await new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open("GET", url, true);
                xhr.responseType = "blob";
                xhr.setRequestHeader("X-Frappe-CSRF-Token", frappe.csrf_token);
                xhr.onload = function () {
                    if (xhr.status === 200) {
                        const contentType = xhr.getResponseHeader("Content-Type") || "";
                        if (contentType.indexOf("application/json") !== -1 || contentType.indexOf("text/html") !== -1) {
                            const reader = new FileReader();
                            reader.onload = function () {
                                try {
                                    const err = JSON.parse(reader.result);
                                    reject(new Error(err.exception || err.message || __("Export failed")));
                                } catch (e) {
                                    reject(new Error(reader.result || __("Export failed")));
                                }
                            };
                            reader.readAsText(xhr.response);
                        } else {
                            resolve(xhr.response);
                        }
                    } else {
                        reject(new Error(__("Export failed (HTTP {0})", [xhr.status])));
                    }
                };
                xhr.onerror = function () {
                    reject(new Error(__("Network error")));
                };
                xhr.send();
            });

            const filename = "batch-" + this.doctype + "-" + this.docnames.length + "docs.xlsx";
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = blobUrl;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(blobUrl);

            frappe.show_alert({ message: __("Excel exported"), indicator: "green" });

            // Record batch logs
            const excelLogArgs = {
                doctype: this.doctype,
                docnames: JSON.stringify(this.docnames),
                design_name: this.current_design,
                params: this.current_params,
                export_type: "Export Excel",
            };
            if (this.auto_match) {
                excelLogArgs.auto_match = 1;
            }
            await frappe.call({
                method: "zhiz_print.api.batch_print.batch_record_print_log",
                args: excelLogArgs,
            });
            this.load_batch_logs();
        } catch (e) {
            console.error("Batch Excel export failed:", e);
            frappe.show_alert({ message: e.message || __("Excel export failed"), indicator: "red" });
        }
    }

    // ==================== Batch Logs ====================

    async load_batch_logs() {
        try {
            const res = await frappe.call({
                method: "zhiz_print.api.batch_print.batch_get_print_logs",
                args: {
                    doctype: this.doctype,
                    docnames: JSON.stringify(this.docnames),
                },
            });

            const data = res.message || {};
            const logs = data.logs || [];
            const totalCount = data.total_count || 0;

            const countEl = document.getElementById("sp-log-count");
            if (countEl) countEl.textContent = totalCount;

            const listEl = document.getElementById("sp-log-list");
            if (!listEl) return;

            if (logs.length === 0) {
                listEl.innerHTML = '<div class="sp-empty-log">' + __("No print records") + "</div>";
                return;
            }

            listEl.innerHTML = "";
            logs.forEach((log) => {
                const item = document.createElement("div");
                item.className = "sp-log-item";

                const time = log.print_time ? frappe.datetime.str_to_user(log.print_time) : "";
                const userFullName = log.user_fullname || log.print_user || "";
                const userImg = log.print_user ? frappe.avatar(log.print_user) : "";
                const exportType = (log.export_type || "Print").trim();
                const docName = log.reference_name || "";

                let typeIcon = "fa-print";
                let typeColor = "#2196f3";
                if (exportType === "Export PDF") {
                    typeIcon = "fa-file-pdf-o";
                    typeColor = "#e53935";
                } else if (exportType === "Export Excel") {
                    typeIcon = "fa-file-excel-o";
                    typeColor = "#43a047";
                }

                item.innerHTML =
                    '<div class="sp-log-line1">' +
                        '<span class="sp-log-count">#' + this.esc(String(log.print_count)) + "</span>" +
                        '<i class="fa ' + typeIcon + ' sp-log-type-icon" style="color:' + typeColor + '" title="' + this.esc(exportType) + '"></i>' +
                        '<span class="sp-log-design">' + this.esc(log.print_design || "") + "</span>" +
                    "</div>" +
                    '<div class="sp-log-line1" style="margin-top:2px">' +
                        '<span class="sp-log-docname" title="' + this.esc(docName) + '">' + this.esc(docName) + "</span>" +
                    "</div>" +
                    '<div class="sp-log-line2">' +
                        userImg +
                        '<span class="sp-log-user">' + this.esc(userFullName) + "</span>" +
                    "</div>" +
                    '<div class="sp-log-line3">' +
                        '<i class="fa fa-clock-o"></i> ' + this.esc(time) +
                    "</div>";

                item.addEventListener("click", () => {
                    window.open("/app/super-print-log/" + encodeURIComponent(log.name), "_blank");
                });

                listEl.appendChild(item);
            });
        } catch (e) {
            console.error("Failed to load batch logs:", e);
        }
    }

    // ==================== Utilities ====================

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

    esc(text) {
        if (!text) return "";
        const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
        return String(text).replace(/[&<>"']/g, (m) => map[m]);
    }
};
