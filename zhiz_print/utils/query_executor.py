# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
import json


MERGED_PREFIX = "||MERGED::"
MERGED_SUFFIX = "||"


def execute_query_code(query_code, filters=None, format_result=True):
	"""Safely execute Python query code"""
	from frappe.utils.safe_exec import safe_exec, get_safe_globals

	_globals = get_safe_globals()
	_globals.update({
		'frappe': frappe,
		'_dict': frappe._dict,
		'json': json,
	})

	params = {}
	if filters:
		if isinstance(filters, str):
			try:
				filters = json.loads(filters)
			except (json.JSONDecodeError, TypeError):
				filters = {}
		params = filters if isinstance(filters, dict) else {}

	_locals = {'result': None, 'filters': params}

	try:
		safe_exec(query_code, _globals, _locals)
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), 'Print query execution failed')
		frappe.throw(_('Query execution failed: {0}').format(str(e)))

	result = _locals.get('result')
	if format_result and result is not None:
		return format_query_result(result)
	return result


def format_query_result(result):
	"""Format query result to unified format"""
	if isinstance(result, (list, tuple)):
		formatted = []
		for item in result:
			if isinstance(item, dict):
				formatted.append(item)
			elif hasattr(item, 'as_dict'):
				formatted.append(item.as_dict())
			elif isinstance(item, (list, tuple)):
				formatted.append(list(item))
			else:
				formatted.append({'value': item})
		return formatted
	elif isinstance(result, dict):
		return [result]
	return result


def parse_parameters(parameters_str):
	"""Parse parameter string to dict"""
	if not parameters_str:
		return {}
	params = {}
	for line in parameters_str.strip().split('\n'):
		line = line.strip()
		if '=' in line:
			key, val = line.split('=', 1)
			params[key.strip()] = val.strip()
	return params


def replace_dynamic_params(params, doc_name, doc_type):
	"""Replace dynamic params {{doc.field}} with actual values"""
	try:
		doc = frappe.get_doc(doc_type, doc_name)
	except Exception:
		return params

	for key, val in list(params.items()):
		if isinstance(val, str) and '{{' in val and '}}' in val:
			field_name = val.replace('{{doc.', '').replace('}}', '').strip()
			if hasattr(doc, field_name):
				params[key] = getattr(doc, field_name)
	return params


def is_merged_cell(value):
	"""Check if value is a merged cell marker"""
	if not value:
		return False
	return isinstance(value, str) and value.startswith(MERGED_PREFIX) and value.endswith(MERGED_SUFFIX)


def extract_master_id(value):
	"""Extract master cell ID from merged marker"""
	if not is_merged_cell(value):
		return None
	return value[len(MERGED_PREFIX):-len(MERGED_SUFFIX)]


def generate_barcode_base64(value, barcode_format='CODE128', width=100, height=40):
	"""Generate barcode SVG base64, pure Python, no external library dependency"""
	if not value:
		return None
	try:
		fmt = (barcode_format or 'CODE128').upper()
		if fmt == 'CODE39':
			svg = _generate_code39_svg(str(value), width, height)
		else:
			svg = _generate_code128_svg(str(value), width, height)
		if svg:
			import base64
			return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode('utf-8')).decode('ascii')
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), 'Barcode generation failed')
	return None


# Code128 encoding table (BSBSBS pattern, each digit represents bar/space width)
_CODE128_PATTERNS = [
	"212222","222122","222221","121223","121322","131222","122213","122312","132212","221213",
	"221312","231212","112232","122132","122231","113222","123122","123221","223211","221132",
	"221231","213212","223112","312131","311222","321122","321221","312212","322112","322211",
	"212123","212321","232121","111323","131123","131321","112313","132113","132311","211313",
	"231113","231311","112133","112331","132131","113123","113321","133121","313121","211331",
	"231131","213113","213311","213131","311123","311321","331121","312113","312311","332111",
	"314111","221411","431111","111224","111422","121124","121421","141122","141221","112214",
	"112412","122114","122411","142112","142211","241211","221114","413111","241112","134111",
	"111242","121142","121241","114212","124112","124211","411212","421112","421211","212141",
	"214121","412121","111143","111341","131141","114113","114311","411113","411311","113141",
	"114131","311141","411131","211412","211214","211232",
	"2331112"  # Stop (index 106)
]

# Code39 encoding table
_CODE39_PATTERNS = {
	'0':'NNNWWNWNN','1':'WNNWNNNNW','2':'NNWWNNNNW','3':'WNWWNNNNN',
	'4':'NNNWWNNNW','5':'WNWWNNNNN','6':'NNWWWNNNN','7':'NNNWNWNNW',
	'8':'WNNWNWNNN','9':'NNWWNWNNN','A':'WNNNNNWNN','B':'NNWNNNNWN',
	'C':'WNWNNNNNN','D':'NNNNWNNWN','E':'WNNNWNNNN','F':'NNNWWNNNN',
	'G':'NNNNNNWWN','H':'WNNNNNWWN','I':'NNWNNNNWN','J':'NNNNWNWWN',
	'K':'WNNNNNNNW','L':'NNWNNNNNW','M':'WNNNNNNNN','N':'NNNNWNNNN',
	'O':'WNNNWNNNW','P':'NNNWWNNNN','Q':'NNNNNNWNW','R':'WNNNNNWNW',
	'S':'NNWNNNWNW','T':'NNNNWWNWN','U':'WNNNNNNNW','V':'NNWNNNNNW',
	'W':'WNNNNNNNN','X':'NNNNWNNNN','Y':'WNNNWNNNN','Z':'NNNWWNNNN',
	'-':'NNNNWNWNN','.':'WNNNWNWNN',' ':'NNWNWNWNN','$':'NNWNWNNWN',
	'/':'NNWNNWNWN','+':'NNWNWNWNN','%':'NNNNWNWWN',
	'*':'NNWNWNWNN',  # Start/Stop
}


def _generate_code128_svg(value, width=100, height=40):
	"""Pure Python Code128B barcode SVG generation (with quiet zone)"""
	# Start Code B = value 104
	encoded = [104]
	for ch in value:
		code = ord(ch)
		if 32 <= code <= 126:
			encoded.append(code - 32)
		else:
			encoded.append(0)

	# Checksum digit
	checksum = encoded[0]
	for i in range(1, len(encoded)):
		checksum += i * encoded[i]
	checksum %= 103
	encoded.append(checksum)
	encoded.append(106)  # Stop

	# Build bar/space width sequence (integer module units)
	module_seq = []
	for val in encoded:
		pattern = _CODE128_PATTERNS[val]
		for ch in pattern:
			module_seq.append(int(ch))

	# Calculate total modules + 10 quiet modules on each side
	quiet = 10
	data_modules = sum(module_seq)
	total_modules = data_modules + quiet * 2

	# Use integer pixels, 1 module = N pixels, ensure scan accuracy
	unit = max(1, int(width / total_modules))
	actual_w = total_modules * unit

	rects = []
	x = quiet * unit  # Left quiet zone
	for i, w in enumerate(module_seq):
		if i % 2 == 0:  # Bar (black)
			rects.append('<rect x="%d" y="0" width="%d" height="%d" fill="black"/>' % (x, w * unit, height))
		x += w * unit

	return '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">%s</svg>' % (actual_w, height, actual_w, height, ''.join(rects))


def _generate_code39_svg(value, width=100, height=40):
	"""Pure Python Code39 barcode SVG generation"""
	encoded = ['*']  # Start
	for ch in value.upper():
		if ch in _CODE39_PATTERNS:
			encoded.append(ch)
	encoded.append('*')  # Stop

	# Code39: N=1 unit, W=3 units, inter-character gap=1 unit
	narrow = 1
	wide = 3
	gap = 1

	module_seq = []
	for char_val in encoded:
		pattern = _CODE39_PATTERNS.get(char_val, _CODE39_PATTERNS['*'])
		for ch in pattern:
			module_seq.append(wide if ch == 'W' else narrow)
		module_seq.append(gap)  # Inter-character gap

	module_seq.pop()  # Remove last gap

	quiet = 10
	data_modules = sum(module_seq)
	total_modules = data_modules + quiet * 2
	unit = max(1, int(width / total_modules))
	actual_w = total_modules * unit

	rects = []
	x = quiet * unit
	for i, w in enumerate(module_seq):
		if i % 2 == 0:  # Bar
			rects.append('<rect x="%d" y="0" width="%d" height="%d" fill="black"/>' % (x, w * unit, height))
		x += w * unit

	return '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">%s</svg>' % (actual_w, height, actual_w, height, ''.join(rects))


def generate_qrcode_base64(value, width=100, height=100):
	"""Generate QR code base64 PNG, scale to specified size"""
	if not value:
		return None
	try:
		import qrcode
		from io import BytesIO
		import base64

		qr = qrcode.QRCode(version=1, box_size=4, border=1)
		qr.add_data(str(value))
		qr.make(fit=True)
		img = qr.make_image(fill_color="black", back_color="white")

		# Scale to target size, ensure image intrinsic size matches display size (WeasyPrint compatible)
		target_w = int(width) if width else img.size[0]
		target_h = int(height) if height else img.size[1]
		if img.size[0] != target_w or img.size[1] != target_h:
			img = img.resize((target_w, target_h))

		buffer = BytesIO()
		img.save(buffer, format='PNG')
		img_data = base64.b64encode(buffer.getvalue()).decode('ascii')
		return f'data:image/png;base64,{img_data}'
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), 'QR code generation failed')
		return None
