frappe.ui.form.on('Super Print Log', {
	refresh: function(frm) {
		if (!frm.doc.print_preview_html) return;

		let field = frm.get_field('preview_render');
		if (!field) return;
		let wrapper = field.$wrapper;
		wrapper.empty();

		let iframe = $('<iframe>').css({
			width: '100%',
			height: '800px',
			border: '1px solid #e0e0e0',
			'border-radius': '6px',
			background: 'white'
		});

		wrapper.append(iframe);

		let iframeDoc = iframe[0].contentDocument || iframe[0].contentWindow.document;
		iframeDoc.open();
		iframeDoc.write(frm.doc.print_preview_html);
		iframeDoc.close();
	}
});
