package main

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/odyrsohn/mlops/autorater/alerting/dedupe"
	"github.com/odyrsohn/mlops/autorater/alerting/dispatch"
)

type fakeDispatcher struct {
	name   string
	alerts []dispatch.Alert
	fail   bool
}

func (f *fakeDispatcher) Name() string { return f.name }

func (f *fakeDispatcher) Dispatch(_ context.Context, a dispatch.Alert) error {
	if f.fail {
		return context.DeadlineExceeded
	}
	f.alerts = append(f.alerts, a)
	return nil
}

// logStore is shared by a captureHandler and every handler derived from it
// via .With(...), so records appended through any derived logger are still
// visible from the original handle the test holds onto.
type logStore struct {
	mu      sync.Mutex
	records []map[string]any
}

// captureHandler records every slog.Record's message and attrs (including
// base attrs attached via .With(...)) so tests can assert on the canonical
// envelope without parsing JSON off stdout.
type captureHandler struct {
	store *logStore
	base  map[string]any
}

func newCaptureHandler() *captureHandler {
	return &captureHandler{store: &logStore{}}
}

func (h *captureHandler) Enabled(context.Context, slog.Level) bool { return true }

func (h *captureHandler) Handle(_ context.Context, r slog.Record) error {
	attrs := map[string]any{"msg": r.Message}
	for k, v := range h.base {
		attrs[k] = v
	}
	r.Attrs(func(a slog.Attr) bool {
		attrs[a.Key] = a.Value.Any()
		return true
	})
	h.store.mu.Lock()
	h.store.records = append(h.store.records, attrs)
	h.store.mu.Unlock()
	return nil
}

func (h *captureHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	base := make(map[string]any, len(h.base)+len(attrs))
	for k, v := range h.base {
		base[k] = v
	}
	for _, a := range attrs {
		base[a.Key] = a.Value.Any()
	}
	return &captureHandler{store: h.store, base: base}
}

func (h *captureHandler) WithGroup(string) slog.Handler { return h }

func (h *captureHandler) find(msg string) map[string]any {
	h.store.mu.Lock()
	defer h.store.mu.Unlock()
	for _, r := range h.store.records {
		if r["msg"] == msg {
			return r
		}
	}
	return nil
}

func setup() (http.Handler, *fakeDispatcher, *fakeDispatcher, *captureHandler) {
	slack := &fakeDispatcher{name: "slack"}
	pd := &fakeDispatcher{name: "pagerduty"}
	cap := newCaptureHandler()
	logger := slog.New(cap).With("service", "alerting", "env", "test")
	h := newHandler(
		dedupe.New(time.Minute),
		[]dispatch.Dispatcher{slack},
		[]dispatch.Dispatcher{slack, pd},
		logger,
	)
	return h, slack, pd, cap
}

func post(h http.Handler, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/alerts", strings.NewReader(body))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	return w
}

func TestHighSeverityGoesToSlackOnly(t *testing.T) {
	h, slack, pd, cap := setup()
	body := `{"fingerprint":"fp1","tenant_id":"acme","severity":"high","failure_mode":"retrieval_failure","score":75,
	          "lang":"es","client_platform":"aaos","client_os_version":"12","serving_model":"claude-sonnet-5"}`
	w := post(h, body)
	if w.Code != http.StatusAccepted {
		t.Fatalf("want 202, got %d: %s", w.Code, w.Body)
	}
	if len(slack.alerts) != 1 || len(pd.alerts) != 0 {
		t.Fatalf("high must page slack only: slack=%d pd=%d", len(slack.alerts), len(pd.alerts))
	}
	// The dispatched alert must carry the slice dims so on-call sees them
	// in the payload itself.
	got := slack.alerts[0]
	if got.Lang != "es" || got.ClientPlatform != "aaos" || got.ServingModel != "claude-sonnet-5" {
		t.Fatalf("dispatched alert missing slice dims: %+v", got)
	}

	dispatched := cap.find("alert_dispatched")
	if dispatched == nil {
		t.Fatal("expected alert_dispatched event")
	}
	if dispatched["service"] != "alerting" || dispatched["env"] != "test" {
		t.Fatalf("alert_dispatched missing base envelope attrs: %+v", dispatched)
	}
	if dispatched["failure_mode"] != "retrieval_failure" || dispatched["tenant_id"] != "acme" {
		t.Fatalf("alert_dispatched missing slice keys: %+v", dispatched)
	}
}

func TestCriticalPagesBothChannels(t *testing.T) {
	h, slack, pd, _ := setup()
	post(h, `{"fingerprint":"fp2","tenant_id":"acme","severity":"critical","failure_mode":"non_terminating_loop","score":92}`)
	if len(slack.alerts) != 1 || len(pd.alerts) != 1 {
		t.Fatalf("critical must page both: slack=%d pd=%d", len(slack.alerts), len(pd.alerts))
	}
}

func TestDuplicateFingerprintSuppressed(t *testing.T) {
	h, slack, _, cap := setup()
	post(h, `{"fingerprint":"fp3","tenant_id":"acme","severity":"high"}`)
	w := post(h, `{"fingerprint":"fp3","tenant_id":"acme","severity":"high"}`)

	if w.Code != http.StatusOK {
		t.Fatalf("duplicate should return 200 suppressed, got %d", w.Code)
	}
	var resp map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["status"] != "suppressed" {
		t.Fatalf("want suppressed, got %v", resp)
	}
	if len(slack.alerts) != 1 {
		t.Fatalf("duplicate must not re-dispatch, slack got %d", len(slack.alerts))
	}

	suppressed := cap.find("alert_suppressed")
	if suppressed == nil || suppressed["tenant_id"] != "acme" {
		t.Fatalf("expected alert_suppressed with tenant_id, got %+v", suppressed)
	}
}

func TestValidation(t *testing.T) {
	h, _, _, _ := setup()
	cases := map[string]struct {
		body string
		code int
	}{
		"malformed":        {"{", http.StatusBadRequest},
		"no fingerprint":   {`{"severity":"high"}`, http.StatusUnprocessableEntity},
		"bad severity":     {`{"fingerprint":"x","severity":"meh"}`, http.StatusUnprocessableEntity},
		"missing severity": {`{"fingerprint":"x"}`, http.StatusUnprocessableEntity},
	}
	for name, tc := range cases {
		if w := post(h, tc.body); w.Code != tc.code {
			t.Errorf("%s: want %d, got %d", name, tc.code, w.Code)
		}
	}
}

func TestAllChannelsFailingReturns502(t *testing.T) {
	slack := &fakeDispatcher{name: "slack", fail: true}
	h := newHandler(dedupe.New(time.Minute), []dispatch.Dispatcher{slack}, []dispatch.Dispatcher{slack}, slog.Default())
	if w := post(h, `{"fingerprint":"fp4","severity":"high"}`); w.Code != http.StatusBadGateway {
		t.Fatalf("want 502, got %d", w.Code)
	}
}
