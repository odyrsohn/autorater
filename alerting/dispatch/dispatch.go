// Package dispatch fans regression alerts out to notification channels.
// Payload shapes match the real Slack webhook and PagerDuty Events v2 APIs;
// with no URL configured a dispatcher runs in mock mode and logs the payload
// it would have sent.
package dispatch

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"
)

// Alert is the normalized regression alert accepted by the webhook.
type Alert struct {
	Fingerprint string  `json:"fingerprint"`
	CaseID      string  `json:"case_id"`
	TenantID    string  `json:"tenant_id"`
	FailureType string  `json:"failure_type"`
	Severity    string  `json:"severity"` // high | critical
	Score       int     `json:"score"`
	Summary     string  `json:"summary"`
	WindowRate  float64 `json:"window_failure_rate,omitempty"`
}

// Dispatcher delivers an alert to one channel.
type Dispatcher interface {
	Name() string
	Dispatch(ctx context.Context, a Alert) error
}

var httpClient = &http.Client{Timeout: 5 * time.Second}

func post(ctx context.Context, name, url string, payload any, logger *slog.Logger) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	if url == "" { // mock mode
		logger.Info("dispatch (mock)", "channel", name, "payload", string(body))
		return nil
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("%s returned %d", name, resp.StatusCode)
	}
	return nil
}

// Slack posts a Block Kit message to an incoming webhook.
type Slack struct {
	WebhookURL string
	Logger     *slog.Logger
}

func (s *Slack) Name() string { return "slack" }

func (s *Slack) Dispatch(ctx context.Context, a Alert) error {
	payload := map[string]any{
		"text": fmt.Sprintf(":rotating_light: [%s] %s regression — tenant %s", a.Severity, a.FailureType, a.TenantID),
		"blocks": []map[string]any{
			{
				"type": "section",
				"text": map[string]string{
					"type": "mrkdwn",
					"text": fmt.Sprintf(
						"*%s regression detected*\n• tenant: `%s`\n• case: `%s`\n• judge score: *%d*\n• %s",
						a.FailureType, a.TenantID, a.CaseID, a.Score, a.Summary,
					),
				},
			},
		},
	}
	return post(ctx, s.Name(), s.WebhookURL, payload, s.Logger)
}

// PagerDuty sends a PagerDuty Events API v2 trigger.
type PagerDuty struct {
	EventsURL  string // https://events.pagerduty.com/v2/enqueue in production
	RoutingKey string
	Logger     *slog.Logger
}

func (p *PagerDuty) Name() string { return "pagerduty" }

func (p *PagerDuty) Dispatch(ctx context.Context, a Alert) error {
	payload := map[string]any{
		"routing_key":  p.RoutingKey,
		"event_action": "trigger",
		"dedup_key":    a.Fingerprint,
		"payload": map[string]any{
			"summary":  fmt.Sprintf("[%s] %s regression, tenant %s, score %d", a.Severity, a.FailureType, a.TenantID, a.Score),
			"source":   "autorater-alerting",
			"severity": "critical",
			"custom_details": map[string]any{
				"case_id":             a.CaseID,
				"window_failure_rate": a.WindowRate,
				"rationale":           a.Summary,
			},
		},
	}
	return post(ctx, p.Name(), p.EventsURL, payload, p.Logger)
}
