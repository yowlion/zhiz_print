// Batch Print Interceptor
// Patches frappe.views.ListView to intercept bulk print action
// When Super Print is enabled for the doctype, redirects to batch-print page

frappe.provide('zhiz_print.batch');

(function patchBulkPrint() {
    if (!frappe.views || !frappe.views.ListView) {
        setTimeout(patchBulkPrint, 100);
        return;
    }

    const origGetActions = frappe.views.ListView.prototype.get_actions_menu_items;
    if (origGetActions._zhiz_print_patched) return;

    frappe.views.ListView.prototype.get_actions_menu_items = function () {
        const items = origGetActions.call(this);

        // Find the Print menu item
        const printItem = items.find(item => {
            const label = typeof item.label === 'string' ? item.label : (item.label?.__str__ || '');
            return label === __('Print') || label === 'Print';
        });

        if (printItem) {
            const origAction = printItem.action;

            printItem.action = () => {
                const checked = this.get_checked_items();
                if (!checked || !checked.length) {
                    frappe.msgprint(__('Select at least 1 record for printing'));
                    return;
                }

                // Check if super print is enabled for this doctype
                const pd = frappe.boot.zhiz_print?.print_designer;
                if (pd && pd.enabled) {
                    let enabled = false;
                    if (!pd.enable_mode || pd.enable_mode === 'Enable for All') {
                        enabled = true;
                    } else if (pd.enable_mode === 'Enable for Specific') {
                        enabled = (pd.enabled_doctypes || []).includes(this.doctype);
                    }

                    if (enabled) {
                        // Check if designs exist for this doctype
                        frappe.call({
                            method: 'zhiz_print.api.batch_print.check_batch_print_enabled',
                            args: { doctype: this.doctype },
                            freeze: true,
                            callback: (r) => {
                                if (r.message?.enabled && r.message?.designs?.length > 0) {
                                    const docnames = checked.map(d => d.name || d);
                                    frappe.route_options = {
                                        doctype: this.doctype,
                                        docnames: docnames,
                                    };
                                    frappe.set_route('batch-print');
                                } else {
                                    // No designs available, fallback to default
                                    origAction();
                                }
                            },
                        });
                        return;
                    }
                }

                // Fallback: use original Frappe bulk print
                origAction();
            };
        }

        return items;
    };

    frappe.views.ListView.prototype.get_actions_menu_items._zhiz_print_patched = true;
})();
