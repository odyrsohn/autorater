package main

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/odyrsohn/mlops/autorater/alerting/dedupe"
	"github.com/odyrsohn/mlops/autorater/alerting/dispatch"
)

type alertHandler struct {
	cache    *dedupe.Cache
	high     []dispatch.Dispatcher
	critical []dispatch.Dispatcher
	logger   *slog.Logger
}

func newHandler(cache *dedupe.Cache, high, critical []dispatch.Dispatcher, logger *slog.Logger) http.Handler {
	h := &alertHandler{cache: cache, high: high, critical: critical, logger: logger}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/alerts", h.handleAlert)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	return mux
}

func (h *alertHandler) handleAlert(w http.ResponseWriter, r *http.Request) {
	var a dispatch.Alert
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 256<<10)).Decode(&a); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON payload"})
		return
	}
	if a.Fingerprint == "" || a.Severity == "" {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "fingerprint and severity are required"})
		return
	}
	if a.Severity != "high" && a.Severity != "critical" {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "severity must be high or critical"})
		return
	}

	admitted, dups := h.cache.Admit(a.Fingerprint)
	if !admitted {
		h.logger.Info("alert_suppressed",
			"fingerprint", a.Fingerprint, "tenant_id", a.TenantID, "failure_mode", a.FailureMode, "duplicates", dups)
		writeJSON(w, http.StatusOK, map[string]any{"status": "suppressed", "duplicates": dups})
		return
	}

	dispatchers := h.high
	if a.Severity == "critical" {
		dispatchers = h.critical
	}
	delivered := make([]string, 0, len(dispatchers))
	for _, d := range dispatchers {
		if err := d.Dispatch(r.Context(), a); err != nil {
			h.logger.Error("dispatch_failed",
				"channel", d.Name(), "fingerprint", a.Fingerprint, "tenant_id", a.TenantID,
				"failure_mode", a.FailureMode, "err", err)
			continue
		}
		delivered = append(delivered, d.Name())
	}
	if len(delivered) == 0 {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "all channels failed"})
		return
	}

	h.logger.Info("alert_dispatched",
		"fingerprint", a.Fingerprint, "tenant_id", a.TenantID, "severity", a.Severity,
		"failure_mode", a.FailureMode, "lang", a.Lang, "client_platform", a.ClientPlatform,
		"client_os_version", a.ClientOSVersion, "serving_model", a.ServingModel, "channels", delivered)
	writeJSON(w, http.StatusAccepted, map[string]any{"status": "dispatched", "channels": delivered})
}

func writeJSON(w http.ResponseWriter, code int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(body)
}
