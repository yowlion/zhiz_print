// Scan Print Console
// Desk entry for the scan-print kiosk: switches, link/QR and inline preview.

frappe.pages["scan-print-console"].on_page_load = function (wrapper) {
    frappe.ui.make_app_page({ parent: wrapper });

    const page = wrapper.page;
    page.set_title(__("Scan Print Console"));

    const main = page.main;
    main.html(
        '<div class="sp-console">' +
            '<div class="sp-console-cards row"></div>' +
            '<div class="sp-console-kiosk" style="margin-top:15px">' +
                '<h6>' + __("Open on scanner device") + "</h6>" +
                '<div class="sp-console-qr text-muted"></div>' +
                '<div class="sp-console-link" style="margin-top:8px"></div>' +
                '<button class="btn btn-primary btn-sm sp-console-open" style="margin-top:8px">' +
                    __("Open Scan Page") + "</button>" +
            "</div>" +
            '<div class="sp-console-preview" style="margin-top:20px">' +
                '<h6>' + __("Kiosk Preview") + "</h6>" +
                '<iframe src="/scan_print" style="width:100%;height:640px;' +
                    'border:1px solid #d1d8dd;border-radius:4px"></iframe>' +
            "</div>" +
        "</div>"
    );

    const kiosk_url = window.location.origin + "/scan_print";
    main.find(".sp-console-open").on("click", () => window.open(kiosk_url, "_blank"));
    main.find(".sp-console-link").html(
        '<a href="' + kiosk_url + '" target="_blank">' +
            frappe.utils.escape_html(kiosk_url) + "</a>"
    );

    const badge = (ok, okTxt, badTxt) =>
        '<span class="badge" style="background:' +
        (ok ? "#98d85b" : "#ff5858") + ';color:#fff">' +
        (ok ? __(okTxt) : __(badTxt)) + "</span>";

    frappe.call({
        method: "zhiz_print.zhiz_print.page.scan_print_console.scan_print_console.console_bootstrap",
        args: { url: kiosk_url },
        callback: (r) => {
            const m = r.message || {};
            main.find(".sp-console-cards").html(
                '<div class="col-md-3"><div class="card"><div class="card-body">' +
                    '<div class="text-muted">' + __("Scan Feature") + "</div>" +
                    badge(m.scan_print_enabled, "Enabled", "Disabled") +
                "</div></div></div>" +
                '<div class="col-md-4"><div class="card"><div class="card-body">' +
                    '<div class="text-muted">' + __("Silent Print Entitlement") + "</div>" +
                    badge(m.entitled, "Granted", "Not granted") +
                    (m.entitled ? "" : ' <small class="text-muted">' +
                        frappe.utils.escape_html(m.reason_code || "") + "</small>") +
                "</div></div></div>"
            );
            const qr = main.find(".sp-console-qr");
            if (m.kiosk_qr) {
                qr.html(
                    '<img src="' + m.kiosk_qr + '" alt="QR" style="width:160px;height:160px;margin-top:6px"/>' +
                    '<div style="max-width:340px">' +
                        __("Scan this QR with the scanner device browser") + "</div>"
                );
            } else {
                qr.text(__("Scan this QR with the scanner device browser"));
            }
        },
    });
};
