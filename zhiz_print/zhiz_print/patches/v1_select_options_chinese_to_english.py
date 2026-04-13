import frappe


def execute():
    """Migrate Chinese Select option values to English in existing database records."""

    # Zprint Setting: print_enable_mode
    _update_select_field(
        "Zprint Setting",
        "print_enable_mode",
        {
            "\u5168\u90e8\u5355\u636e\u542f\u7528": "Enable for All",
            "\u6307\u5b9a\u5355\u636e\u542f\u7528": "Enable for Specific",
        },
    )

    # Super Print Design Item: row_type
    _update_select_field(
        "Super Print Design Item",
        "row_type",
        {
            "\u666e\u901a\u884c": "Normal Row",
            "\u91cd\u590d\u6807\u9898\u884c": "Repeat Title Row",
            "\u6570\u636e\u9a71\u52a8\u884c": "Data-Driven Row",
        },
    )

    # Super Print Design Item: row_display
    _update_select_field(
        "Super Print Design Item",
        "row_display",
        {
            "\u81ea\u52a8\u6362\u884c": "Auto Wrap",
            "\u56fa\u5b9a\u884c\u9ad8": "Fixed Height",
            "\u81ea\u52a8\u7f29\u5c0f\u5b57\u4f53": "Auto Shrink Font",
        },
    )

    # Super Print Log: export_type
    _update_select_field(
        "Super Print Log",
        "export_type",
        {
            "\u6253\u5370": "Print",
            "\u5bfc\u51faPDF": "Export PDF",
            "\u5bfc\u51faExcel": "Export Excel",
        },
    )


def _update_select_field(doctype, fieldname, mapping):
    """Update select field values from Chinese to English.

    For single DocTypes, update directly.
    For child table DocTypes, update via SQL since they have no single-doc structure.
    """
    meta = frappe.get_meta(doctype)

    if meta.issingle:
        # Single DocType: stored in tabSingles
        for old_val, new_val in mapping.items():
            frappe.db.sql(
                "UPDATE `tabSingles` SET value = %s "
                "WHERE doctype = %s AND field = %s AND value = %s",
                (new_val, doctype, fieldname, old_val),
            )
    else:
        # Child table / regular DocType: stored in its own table
        table_name = f"tab{doctype}"
        for old_val, new_val in mapping.items():
            frappe.db.sql(
                f"UPDATE `{table_name}` SET `{fieldname}` = %s "
                f"WHERE `{fieldname}` = %s",
                (new_val, old_val),
            )

    frappe.db.commit()
