//go:build linux

package main

import (
	"context"
	"errors"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func cpuTimes() (uint64, uint64, bool) {
	data, err := os.ReadFile("/proc/stat")
	if err != nil {
		return 0, 0, false
	}
	fields := strings.Fields(strings.SplitN(string(data), "\n", 2)[0])
	if len(fields) < 6 {
		return 0, 0, false
	}
	var idle, total uint64
	for i, v := range fields[1:] {
		if i >= 8 {
			break
		}
		n, err := strconv.ParseUint(v, 10, 64)
		if err != nil {
			return 0, 0, false
		}
		total += n
		if i == 3 || i == 4 {
			idle += n
		}
	}
	return idle, total, true
}
func systemInventory() (string, map[string]float64) {
	values := map[string]float64{}
	idle, total, ok := cpuTimes()
	time.Sleep(200 * time.Millisecond)
	nextIdle, nextTotal, nextOK := cpuTimes()
	if ok && nextOK && nextTotal > total && nextIdle >= idle {
		values["cpu_percent"] = 100 * (1 - float64(nextIdle-idle)/float64(nextTotal-total))
	}
	data, _ := os.ReadFile("/proc/meminfo")
	memory := map[string]float64{}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 {
			n, _ := strconv.ParseFloat(fields[1], 64)
			memory[fields[0]] = n * 1024
		}
	}
	if memory["MemTotal:"] > 0 {
		values["memory_total_bytes"] = memory["MemTotal:"]
		values["memory_used_bytes"] = memory["MemTotal:"] - memory["MemAvailable:"]
	}
	var disk syscall.Statfs_t
	if syscall.Statfs("/", &disk) == nil {
		values["disk_total_bytes"] = float64(disk.Blocks) * float64(disk.Bsize)
		values["disk_used_bytes"] = float64(disk.Blocks-disk.Bfree) * float64(disk.Bsize)
	}
	data, _ = os.ReadFile("/proc/uptime")
	fields := strings.Fields(string(data))
	if len(fields) > 0 {
		n, _ := strconv.ParseFloat(fields[0], 64)
		values["uptime_seconds"] = n
	}
	data, _ = os.ReadFile("/proc/sys/kernel/osrelease")
	return strings.TrimSpace(string(data)), values
}
func runPlatformService(_ func(context.Context) error) error {
	return errors.New("Windows service mode is unsupported on Linux")
}
