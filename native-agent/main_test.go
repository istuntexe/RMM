package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestConfigRejectsUnsafeServer(t *testing.T) {
	for _, server := range []string{"http://example.com", "https://user:pass@example.com", "https://example.com/path", "https://example.com?token=x"} {
		config := DeviceConfig{server, "device", strings.Repeat("x", 40)}
		data, _ := json.Marshal(config)
		path := filepath.Join(t.TempDir(), "state.json")
		os.WriteFile(path, data, 0600)
		if _, err := loadConfig(path); err == nil {
			t.Errorf("accepted unsafe server %s", server)
		}
	}
}
func TestHeartbeatIncludesHealthAndDeviceCredential(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/heartbeat" || r.Header.Get("Authorization") != "Bearer "+strings.Repeat("d", 40) {
			t.Error("wrong endpoint or credential")
		}
		var inventory Inventory
		if json.NewDecoder(r.Body).Decode(&inventory) != nil {
			t.Error("invalid JSON")
		}
		if inventory.Hostname == "" || inventory.AgentVersion != version {
			t.Error("missing identity")
		}
		for _, metric := range []string{"cpu_percent", "memory_total_bytes", "memory_used_bytes", "disk_total_bytes", "disk_used_bytes", "uptime_seconds"} {
			if _, ok := inventory.Metrics[metric]; !ok {
				t.Errorf("missing %s", metric)
			}
		}
		w.Write([]byte(`{"status":"ok"}`))
	}))
	defer server.Close()
	if err := heartbeat(context.Background(), DeviceConfig{server.URL, "device", strings.Repeat("d", 40)}); err != nil {
		t.Fatal(err)
	}
}
func TestHeartbeatDoesNotFollowRedirects(t *testing.T) {
	visited := false
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { visited = true }))
	defer target.Close()
	source := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, http.StatusFound) }))
	defer source.Close()
	if err := heartbeat(context.Background(), DeviceConfig{source.URL, "device", strings.Repeat("d", 40)}); err == nil {
		t.Error("redirect was accepted")
	}
	if visited {
		t.Error("credential-bearing redirect was followed")
	}
}
