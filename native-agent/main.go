// Hybrid RMM inventory agent. No remote execution or screen capture.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const version = "0.2.0"

type DeviceConfig struct {
	Server string `json:"server"`
	ID     string `json:"id"`
	Token  string `json:"device_token"`
}
type Inventory struct {
	Hostname     string             `json:"hostname"`
	OS           string             `json:"os"`
	Release      string             `json:"release"`
	Architecture string             `json:"architecture"`
	AgentVersion string             `json:"agent_version"`
	Metrics      map[string]float64 `json:"metrics,omitempty"`
}

func loadConfig(path string) (DeviceConfig, error) {
	var config DeviceConfig
	file, err := os.Open(path)
	if err != nil {
		return config, fmt.Errorf("open device configuration: %w", err)
	}
	defer file.Close()
	if json.NewDecoder(io.LimitReader(file, 16385)).Decode(&config) != nil {
		return config, errors.New("invalid device configuration")
	}
	endpoint, err := url.Parse(config.Server)
	if err != nil || endpoint.Hostname() == "" || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" || (endpoint.Path != "" && endpoint.Path != "/") {
		return config, errors.New("server must be an HTTPS origin without credentials or path")
	}
	loopback := endpoint.Hostname() == "127.0.0.1" || endpoint.Hostname() == "localhost" || endpoint.Hostname() == "::1"
	if endpoint.Scheme != "https" && !(endpoint.Scheme == "http" && loopback) {
		return config, errors.New("HTTPS required except on loopback")
	}
	if len(config.Token) < 32 || len(config.Token) > 512 || config.ID == "" {
		return config, errors.New("invalid device credential")
	}
	config.Server = strings.TrimRight(config.Server, "/")
	return config, nil
}
func heartbeat(ctx context.Context, config DeviceConfig) error {
	hostname, err := os.Hostname()
	if err != nil {
		return errors.New("could not read hostname")
	}
	release, metrics := systemInventory()
	platform := runtime.GOOS
	if platform == "windows" {
		platform = "Windows"
	} else if platform == "linux" {
		platform = "Linux"
	}
	payload, err := json.Marshal(Inventory{hostname, platform, release, runtime.GOARCH, version, metrics})
	if err != nil {
		return errors.New("could not encode inventory")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, config.Server+"/api/heartbeat", bytes.NewReader(payload))
	if err != nil {
		return errors.New("invalid server request")
	}
	request.Header.Set("Authorization", "Bearer "+config.Token)
	request.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 20 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(request)
	if err != nil {
		return errors.New("heartbeat connection failed; check server connectivity and TLS")
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 16384))
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("heartbeat rejected (HTTP %d)", response.StatusCode)
	}
	return nil
}
func reportLoop(ctx context.Context, config DeviceConfig, once bool) error {
	for {
		err := heartbeat(ctx, config)
		if once {
			return err
		}
		if err != nil {
			log.Print(err)
		} else {
			log.Print("Device health reported")
		}
		timer := time.NewTimer(60 * time.Second)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil
		case <-timer.C:
		}
	}
}
func main() {
	defaultPath := filepath.Join("data", "device.json")
	if runtime.GOOS == "windows" {
		defaultPath = filepath.Join(os.Getenv("ProgramData"), "HybridRMM", "device.json")
	}
	state := flag.String("state", defaultPath, "Device configuration file")
	once := flag.Bool("once", false, "Send one health report and exit")
	consent := flag.Bool("consent", false, "Authorize hostname, OS, CPU, RAM, disk and uptime reporting")
	service := flag.Bool("service", false, "Run as the registered Windows inventory service")
	flag.Parse()
	if !*consent {
		fmt.Fprintln(os.Stderr, "--consent is required for device health reporting")
		os.Exit(2)
	}
	config, err := loadConfig(*state)
	if err != nil {
		log.Print(err)
		os.Exit(1)
	}
	run := func(ctx context.Context) error { return reportLoop(ctx, config, *once) }
	if *service {
		err = runPlatformService(run)
	} else {
		ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
		defer cancel()
		err = run(ctx)
	}
	if err != nil {
		log.Print(err)
		os.Exit(1)
	}
}
