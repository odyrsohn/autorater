package main

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
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

func setup() (http.Handler, *fakeDispatcher, *fakeDispatcher) {
	slack := &fakeDispatcher{name: "slack"}
	pd := &fakeDispatcher{name: "pagerduty"}
	h := newHandler(
		dedupe.New(time.Minute),
		[]dispatch.Dispatcher{slack},
		[]dispatch.Dispatcher{slack, pd},
		slog.Default(),
	)
	return h, slack, pd
}

func post(h http.Handler, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/alerts", strings.NewReader(body))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	return w
}

func TestHighSeverityGoesToSlackOnly(t *testing.T) {
	h, slack, pd := setup()
	w := post(h, `{"fingerprint":"fp1","severity":"high","failure_type":"retrieval_failure","score":75}`)
	if w.Code != http.StatusAccepted {
		t.Fatalf("want 202, got %d: %s", w.Code, w.Body)
	}
	if len(slack.alerts) != 1 || len(pd.alerts) != 0 {
		t.Fatalf("high must page slack only: slack=%d pd=%d", len(slack.alerts), len(pd.alerts))
	}
}

func TestCriticalPagesBothChannels(t *testing.T) {
	h, slack, pd := setup()
	post(h, `{"fingerprint":"fp2","severity":"critical","failure_type":"non_terminating_loop","score":92}`)
	if len(slack.alerts) != 1 || len(pd.alerts) != 1 {
		t.Fatalf("critical must page both: slack=%d pd=%d", len(slack.alerts), len(pd.alerts))
	}
}

func TestDuplicateFingerprintSuppressed(t *testing.T) {
	h, slack, _ := setup()
	post(h, `{"fingerprint":"fp3","severity":"high"}`)
	w := post(h, `{"fingerprint":"fp3","severity":"high"}`)

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
}

func TestValidation(t *testing.T) {
	h, _, _ := setup()
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
