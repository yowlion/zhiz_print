package erpclient

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"sort"
	"strconv"
)

// CanonicalJSON serialises request params exactly the way the ERP server
// rebuilds them before HMAC verification (zhiz_print/api/scan_print.py
// _canonical_body):
//
//	json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
//
// Go's encoding/json is deliberately NOT used for signing because it
// HTML-escapes <, >, & and always escapes U+2028/U+2029, none of which
// Python escapes - any byte difference breaks the signature. All values
// are JSON strings (the server requires "ALL values as JSON strings"),
// which keeps this serialiser trivial and byte-exact.
func CanonicalJSON(params map[string]string) string {
	keys := make([]string, 0, len(params))
	for k := range params {
		keys = append(keys, k)
	}
	// Byte-wise sort matches Python sort_keys for ASCII keys; every param
	// name used by the agent protocol is ASCII.
	sort.Strings(keys)

	out := make([]byte, 0, 256)
	out = append(out, '{')
	for i, k := range keys {
		if i > 0 {
			out = append(out, ',')
		}
		out = appendJSONString(out, k)
		out = append(out, ':')
		out = appendJSONString(out, params[k])
	}
	return string(append(out, '}'))
}

// Sign computes sig = hex(HMAC_SHA256(api_secret, timestamp + "." + body))
// exactly as the server expects it (scan_print.py _hmac_station_from_params).
func Sign(secret, timestamp, canonicalBody string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(timestamp))
	mac.Write([]byte("."))
	mac.Write([]byte(canonicalBody))
	return hex.EncodeToString(mac.Sum(nil))
}

// appendJSONString escapes s following the Python json encoder rules with
// ensure_ascii=False:
//   - `"` and `\` get a backslash prefix
//   - \n \r \t \b \f use their short escapes
//   - other control bytes (< 0x20) use \u00xx with lowercase hex
//   - every other byte (incl. UTF-8 continuation bytes, DEL, U+2028) is raw
func appendJSONString(out []byte, s string) []byte {
	out = append(out, '"')
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c == '"':
			out = append(out, '\\', '"')
		case c == '\\':
			out = append(out, '\\', '\\')
		case c == '\n':
			out = append(out, '\\', 'n')
		case c == '\r':
			out = append(out, '\\', 'r')
		case c == '\t':
			out = append(out, '\\', 't')
		case c == '\b':
			out = append(out, '\\', 'b')
		case c == '\f':
			out = append(out, '\\', 'f')
		case c < 0x20:
			const hexDigits = "0123456789abcdef"
			out = append(out, '\\', 'u', '0', '0', hexDigits[c>>4], hexDigits[c&0x0f])
		default:
			out = append(out, c)
		}
	}
	return append(out, '"')
}

// TimestampFor returns the wire timestamp: local epoch seconds corrected by
// the persisted server clock offset (plan 6-2; zhiz_jwhost sync_client
// precedent). The server enforces +/-300s against its own clock.
func TimestampFor(localNow int64, offsetSec int64) string {
	return strconv.FormatInt(localNow+offsetSec, 10)
}
