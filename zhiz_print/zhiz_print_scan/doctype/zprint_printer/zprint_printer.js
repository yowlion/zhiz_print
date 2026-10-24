// Copyright (c) 2026
// For license information, please see license.txt

frappe.ui.form.on('Zprint Printer', {
    refresh(frm) {
        // Channel B only: queue an IPP Get-Printer-Attributes probe. Needed
        // BEFORE enabling - the enable gate refuses a channel B printer
        // without a capability snapshot advertising application/pdf.
        if (frm.doc.channel === 'B') {
            frm.add_custom_button(__('Probe Capability'), function() {
                frappe.call({
                    method: 'zhiz_print.zhiz_print_scan.doctype.zprint_printer.zprint_printer.probe_capability',
                    args: { printer: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Queuing capability probe...'),
                    callback(r) {
                        if (r.message && r.message.ok) {
                            frappe.show_alert({
                                message: __('Capability probe queued'),
                                indicator: 'blue'
                            });
                            setTimeout(() => frm.reload_doc(), 3000);
                        }
                    }
                });
            });
        }
    }
});
