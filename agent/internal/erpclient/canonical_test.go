package erpclient

import "testing"

// Expected values were produced with Python 3:
//
//	python3 -c "import json,sys; print(json.dumps(json.loads(sys.argv[1]),
//	    sort_keys=True, separators=(',',':'), ensure_ascii=False))" '<payload>'
//
// Any divergence here is a signature break against
// zhiz_print/api/scan_print.py _canonical_body.
func TestCanonicalJSONMatchesPython(t *testing.T) {
	cases := []struct {
		name string
		in   map[string]string
		want string
	}{
		{
			name: "plain params",
			in:   map[string]string{"api_key": "k1", "timestamp": "1730000000"},
			want: `{"api_key":"k1","timestamp":"1730000000"}`,
		},
		{
			name: "html chars must stay raw",
			in:   map[string]string{"a": "<b>&</b>"},
			want: `{"a":"<b>&</b>"}`,
		},
		{
			name: "quotes and backslash",
			in:   map[string]string{"a": `x"y\z`},
			want: `{"a":"x\"y\\z"}`,
		},
		{
			name: "control chars short escapes then u-escapes",
			in:   map[string]string{"a": "\n\r\t\b\f\x01\x1f"},
			want: `{"a":"\n\r\t\b\f\u0001\u001f"}`,
		},
		{
			name: "raw utf-8 (ensure_ascii false)",
			in:   map[string]string{"scan_code": "工位-001"},
			want: `{"scan_code":"工位-001"}`,
		},
		{
			name: "solidus never escaped",
			in:   map[string]string{"u": "a/b"},
			want: `{"u":"a/b"}`,
		},
		{
			name: "empty map",
			in:   map[string]string{},
			want: `{}`,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := CanonicalJSON(tc.in); got != tc.want {
				t.Fatalf("CanonicalJSON()\n got %q\nwant %q", got, tc.want)
			}
		})
	}
}

func TestSignIsDeterministic(t *testing.T) {
	body := CanonicalJSON(map[string]string{"api_key": "k", "timestamp": "1"})
	a := Sign("secret", "1", body)
	b := Sign("secret", "1", body)
	if a != b || len(a) != 64 {
		t.Fatalf("Sign not deterministic/hex64: %q vs %q", a, b)
	}
	if a == Sign("other", "1", body) {
		t.Fatal("Sign ignores the secret")
	}
}

func TestVersionBelowMirrorsServer(t *testing.T) {
	// Mirror of api/agent.py _version_below: numeric dot compare, unparseable
	// values never block.
	cases := []struct {
		actual, minimum string
		want            bool
	}{
		{"1.0.0", "1.0.1", true},
		{"1.0.2", "1.0.1", false},
		{"1.0", "1.0.0", true},
		{"2.0", "1.99.99", false},
		{"", "1.0", false},
		{"1.0", "", false},
		{"dev", "1.0", false},
		{"1.0.0", "garbage", false},
	}
	for _, tc := range cases {
		if got := VersionBelow(tc.actual, tc.minimum); got != tc.want {
			t.Errorf("VersionBelow(%q, %q) = %v, want %v", tc.actual, tc.minimum, got, tc.want)
		}
	}
}
