# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
import json
import os
import re


MERGED_PREFIX = "||MERGED::"
MERGED_SUFFIX = "||"


@frappe.whitelist()
def execute_query_code(query_code, filters=None, parameters=None, format_result=True, extra_globals=None):
	"""Safely execute Python query code or call a function path"""
	if _is_function_path(query_code):
		return _call_query_function(query_code, filters)

	from frappe.utils.safe_exec import safe_exec
	from frappe.query_builder.functions import Sum, Count, Avg, Max, Min, Round, Concat, Coalesce, Abs
	from frappe.query_builder import Column, functions
	from frappe.query_builder.custom import GROUP_CONCAT

	_globals = {
		'qb': frappe.qb,
		'DocType': frappe.qb.DocType,
		'desc': frappe.qb.desc,
		'asc': frappe.qb.asc,
		'json': json,
		'_dict': frappe._dict,
		'filters': {},
		'result': None,
		'Sum': Sum,
		'Count': Count,
		'Avg': Avg,
		'Max': Max,
		'Min': Min,
		'Round': Round,
		'Concat': Concat,
		'Coalesce': Coalesce,
		'Abs': Abs,
		'GROUP_CONCAT': GROUP_CONCAT,
		'Column': Column,
		'functions': functions,
	}

	if extra_globals and isinstance(extra_globals, dict):
		_globals.update(extra_globals)

	params = {}
	if parameters:
		params = parse_parameters(parameters)
	if filters:
		if isinstance(filters, str):
			try:
				filters = json.loads(filters)
			except (json.JSONDecodeError, TypeError):
				filters = {}
		if isinstance(filters, dict):
			params.update(filters)

	_locals = {'result': None, 'filters': params}

	try:
		safe_exec(query_code, _globals, _locals)
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), 'Print query execution failed')
		frappe.throw('Query execution failed: {0}'.format(str(e)))

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


def _is_function_path(code):
	"""Check if query_code is a Python function path like 'app.module.file.function'"""
	if not code or not isinstance(code, str):
		return False
	code = code.strip()
	if not code or '\n' in code or ' ' in code:
		return False
	if any(ch in code for ch in ('=', '(', ')', '{', '}', ':', '"', "'")):
		return False
	for ch in code:
		if not (ch.isalnum() or ch in ('.', '_')):
			return False
	return '.' in code and not code.startswith('.')


def _call_query_function(func_path, kwargs=None):
	"""Import and call a Python function, passing kwargs"""
	if kwargs is None:
		kwargs = {}

	module_path = '.'.join(func_path.split('.')[:-1])
	func_name = func_path.split('.')[-1]

	try:
		module = frappe.get_module(module_path)
		if module is None:
			module = __import__(module_path, fromlist=[func_name])
		func = getattr(module, func_name)
		result = func(**kwargs)
	except Exception:
		frappe.log_error(frappe.get_traceback(), 'Query function call failed: %s' % func_path)
		return []

	if result is None:
		return []

	# Convert list-of-lists to list of dicts (c0, c1, c2, ...)
	if isinstance(result, (list, tuple)) and result and isinstance(result[0], (list, tuple)):
		col_count = len(result[0])
		result = [{'c%d' % i: row[i] for i in range(col_count)} for row in result]

	return format_query_result(result)


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


def generate_barcode_base64(value, barcode_format='CODE128', width=100, height=40,
                            show_text=True, text_size=10):
	"""Generate barcode base64 image (PNG via python-barcode, fallback to built-in SVG).

	v15.23: 主路径切 python-barcode,支持 22 种码制与条码下方文本(write_text);
	码制输入约束不满足(如 EAN13 位数)时抛错 → 返回 None,由调用方回退显示原文。
	无 Pillow/python-barcode 的环境回退内置纯 Python 实现(仅 CODE128/39,无文本)。"""
	if not value:
		return None
	fmt = (barcode_format or 'CODE128').strip()
	try:
		provider = BARCODE_FORMATS.get(fmt.upper()) or BARCODE_FORMATS.get(fmt.lower())
		if provider:
			import base64
			from io import BytesIO
			import barcode as _barcode
			from barcode.writer import ImageWriter
			bcode = _barcode.get(provider, str(value), writer=ImageWriter())
			# 尺寸单位(python-barcode 以 mm 计,300dpi 渲染,mm2px≈11.81):
			#   module_height = 条纹高度(mm);文本高度 = pt2mm(font_size)/2;text_distance = 文本-条纹间距
			# 目标总高 H:文本开启时按"条纹:文本"≈3:1 分配并留间距,确保文本不被条纹遮挡;
			# 宽度:module_width 联动设计器目标宽 —— 内容短放大模块宽逼近目标宽,内容长取
			# GS1 最小可读 X 维度(0.33mm)保扫码率,渲染后由 CSS max-width 适配单元格
			_px2mm = 25.4 / 300.0
			total_h = max(30, int(height or 40))
			fs_pt = max(6, int(text_size or 10)) if show_text else 0
			text_h_mm = (fs_pt * 25.4 / 72.0) / 2.0 if fs_pt else 0.0
			dist_mm = max(0.8, fs_pt * 0.08) if fs_pt else 0.0
			margin_mm = 0.6
			bars_h_mm = max(4.0, total_h * _px2mm - text_h_mm - dist_mm - margin_mm * 2)
			total_w = max(60, int(width or 100))
			try:
				_built = bcode.build()
				# build() 返回 list[str](每行一个模块串),宽度按首行模块数
				modules = len(_built[0]) if _built else 0
			except Exception:
				modules = 0
			mw = max(0.33, min(total_w * _px2mm / max(1, modules + 4), 1.2)) if modules else 0.5
			fp = BytesIO()
			bcode.write(fp, options={
				'write_text': bool(show_text),
				'font_size': fs_pt,
				'text_distance': dist_mm,
				'module_height': bars_h_mm,
				'module_width': round(mw, 3),
				'quiet_zone': round(max(2.0, mw * 3), 2),
				'margin_top': margin_mm,
				'margin_bottom': margin_mm,
				'center_text': True,
			})
			return 'data:image/png;base64,' + base64.b64encode(fp.getvalue()).decode('ascii')
	except Exception as e:
		# 码制约束不满足等业务性失败:记录后返回 None(调用方显示原文),
		# 不再走 fallback(用户明确选了该码制,静默换码制更危险)
		if _is_barcode_input_error(e):
			frappe.log_error("barcode={0} value={1!r}: {2}".format(
				fmt, str(value)[:50], str(e)), 'Barcode input validation failed')
			return None
		frappe.log_error(frappe.get_traceback(), 'Barcode generation failed')
	# fallback: 无库环境用内置纯 Python 实现(CODE128/39,SVG,无文本)
	try:
		u = (fmt or '').upper()
		if u == 'CODE39':
			svg = _generate_code39_svg(str(value), width, height)
		else:
			svg = _generate_code128_svg(str(value), width, height)
		if svg:
			import base64
			return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode('utf-8')).decode('ascii')
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), 'Barcode generation failed')
	return None


# python-barcode 22 码制映射(设计器下拉值 → provider 名)
BARCODE_FORMATS = {
	# 通用
	'CODE128': 'code128', 'CODE39': 'code39', 'CODABAR': 'codabar', 'NW-7': 'nw-7',
	# 零售
	'EAN-13': 'ean13', 'EAN-13-GUARD': 'ean13-guard', 'EAN-8': 'ean8', 'EAN-8-GUARD': 'ean8-guard',
	'UPC-A': 'upca', 'JAN': 'jan',
	# 包装物流
	'EAN-14': 'ean14', 'ITF': 'itf', 'GS1-128': 'gs1_128',
	# 出版
	'ISBN-13': 'isbn13', 'ISBN-10': 'isbn10', 'ISSN': 'issn',
	# 医药/标准
	'PZN': 'pzn', 'GS1': 'gs1', 'GTIN': 'gtin',
	# 兼容别名
	'EAN': 'ean13', 'EAN13': 'ean13', 'UPC': 'upca',
}


def _is_barcode_input_error(exc):
	"""区分『输入不满足码制约束』(返回 None 显示原文)与『环境/库故障』(走 fallback)。
	python-barcode 约束类异常:NumberIllegalCharacter / NumberOfDigits /
	BarcodeNotFoundError(码制名错)等,均为 ValueError 族或明确 message。"""
	msg = str(exc)
	markers = ('number of digits', 'can not be encoded', 'not known',
	           'illegal character', 'invalid character', 'must contain only')
	try:
		from barcode.errors import BarcodeNotFoundError
		if isinstance(exc, BarcodeNotFoundError):
			return True
	except Exception:
		pass
	return any(m in msg.lower() for m in markers)


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


# Maximum image size to embed as base64 data URI (10MB) — larger images fall back to URL mode
IMAGE_BASE64_MAX_SIZE = 10 * 1024 * 1024


def image_to_base64_src(src_value):
	"""Convert image URL or local file path to base64 data URI for self-contained PDF rendering.

	Supported inputs:
	- ``data:`` URI (returned as-is, idempotent)
	- ``http://`` / ``https://`` URLs (downloaded via requests)
	- ``/files/...`` Frappe public files (resolved against site public path)
	- ``/private/files/...`` Frappe private files (resolved against site private path)

	Falls back to the original src on any failure so rendering degrades to URL mode gracefully.
	Returns None if input is empty.
	"""
	if not src_value:
		return None

	src = src_value.strip()
	if not src:
		return None

	if src.startswith('data:'):
		return src

	try:
		import mimetypes
		import base64

		if src.startswith(('http://', 'https://')):
			import requests
			resp = requests.get(src, timeout=10, verify=False, allow_redirects=True)
			resp.raise_for_status()
			img_data = resp.content
			mime = (resp.headers.get('Content-Type') or '').split(';')[0].strip()
			if not mime or not mime.startswith('image/'):
				mime = mimetypes.guess_type(src)[0] or 'image/png'
		elif src.startswith('/private/files/'):
			file_name = os.path.basename(src)
			file_path = os.path.join(frappe.get_site_path('private', 'files'), file_name)
			with open(file_path, 'rb') as f:
				img_data = f.read()
			mime = mimetypes.guess_type(file_path)[0] or 'image/png'
		elif src.startswith('/files/'):
			file_name = os.path.basename(src)
			file_path = os.path.join(frappe.get_site_path('public', 'files'), file_name)
			with open(file_path, 'rb') as f:
				img_data = f.read()
			mime = mimetypes.guess_type(file_path)[0] or 'image/png'
		else:
			return src

		if len(img_data) > IMAGE_BASE64_MAX_SIZE:
			frappe.log_error(
				message=f'Size={len(img_data)} > {IMAGE_BASE64_MAX_SIZE}',
				title=f'Image to base64 skipped (oversize): {src[:80]}'
			)
			return src

		encoded = base64.b64encode(img_data).decode('ascii')
		return f'data:{mime};base64,{encoded}'
	except Exception:
		frappe.log_error(frappe.get_traceback(), f'Image to base64 failed: {src[:80]}')
		return src
