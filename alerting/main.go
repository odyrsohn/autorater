// Command alerting runs the regression-alert webhook: it receives severe
// cases from the Python mining worker, deduplicates them by fingerprint,
// and routes them to Slack (high) or Slack+PagerDuty (critical).
package main

import (
	"errors"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/odyrsohn/mlops/autorater/alerting/dedupe"
	"github.com/odyrsohn/mlops/autorater/alerting/dispatch"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))

	handler := newHandler(
		dedupe.New(15*time.Minute),
		[]dispatch.Dispatcher{&dispatch.Slack{WebhookURL: os.Getenv("SLACK_WEBHOOK_URL"), Logger: logger}},
		[]dispatch.Dispatcher{
			&dispatch.Slack{WebhookURL: os.Getenv("SLACK_WEBHOOK_URL"), Logger: logger},
			&dispatch.PagerDuty{
				EventsURL:  os.Getenv("PAGERDUTY_EVENTS_URL"),
				RoutingKey: os.Getenv("PAGERDUTY_ROUTING_KEY"),
				Logger:     logger,
			},
		},
		logger,
	)

	srv := &http.Server{
		Addr:              envOr("LISTEN_ADDR", ":8070"),
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
	}
	logger.Info("alerting engine listening", "addr", srv.Addr)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Error("server failed", "err", err)
		os.Exit(1)
	}
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
