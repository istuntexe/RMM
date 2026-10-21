//go:build windows

package main

import (
	"context"
	"errors"
	"fmt"
	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"os"
	"time"
	"unsafe"
)

var kernel = windows.NewLazySystemDLL("kernel32.dll")
var getSystemTimes = kernel.NewProc("GetSystemTimes")
var getMemory = kernel.NewProc("GlobalMemoryStatusEx")
var getDisk = kernel.NewProc("GetDiskFreeSpaceExW")
var getTickCount = kernel.NewProc("GetTickCount64")

type memoryStatus struct {
	Length               uint32
	Load                 uint32
	TotalPhys            uint64
	AvailPhys            uint64
	TotalPageFile        uint64
	AvailPageFile        uint64
	TotalVirtual         uint64
	AvailVirtual         uint64
	AvailExtendedVirtual uint64
}

func cpuTimes() (uint64, uint64, bool) {
	var idle, kernelTime, user windows.Filetime
	ok, _, _ := getSystemTimes.Call(uintptr(unsafe.Pointer(&idle)), uintptr(unsafe.Pointer(&kernelTime)), uintptr(unsafe.Pointer(&user)))
	convert := func(t windows.Filetime) uint64 { return uint64(t.HighDateTime)<<32 | uint64(t.LowDateTime) }
	return convert(idle), convert(kernelTime) + convert(user), ok != 0
}
func systemInventory() (string, map[string]float64) {
	values := map[string]float64{}
	idle, total, ok := cpuTimes()
	time.Sleep(200 * time.Millisecond)
	nextIdle, nextTotal, nextOK := cpuTimes()
	if ok && nextOK && nextTotal > total && nextIdle >= idle {
		values["cpu_percent"] = 100 * (1 - float64(nextIdle-idle)/float64(nextTotal-total))
	}
	mem := memoryStatus{}
	mem.Length = uint32(unsafe.Sizeof(mem))
	if ok, _, _ := getMemory.Call(uintptr(unsafe.Pointer(&mem))); ok != 0 {
		values["memory_total_bytes"] = float64(mem.TotalPhys)
		values["memory_used_bytes"] = float64(mem.TotalPhys - mem.AvailPhys)
	}
	drive := os.Getenv("SystemDrive")
	if drive == "" {
		drive = "C:"
	}
	root, _ := windows.UTF16PtrFromString(drive + `\`)
	var available, totalDisk, free uint64
	if ok, _, _ := getDisk.Call(uintptr(unsafe.Pointer(root)), uintptr(unsafe.Pointer(&available)), uintptr(unsafe.Pointer(&totalDisk)), uintptr(unsafe.Pointer(&free))); ok != 0 {
		values["disk_total_bytes"] = float64(totalDisk)
		values["disk_used_bytes"] = float64(totalDisk - free)
	}
	ticks, _, _ := getTickCount.Call()
	values["uptime_seconds"] = float64(ticks) / 1000
	v := windows.RtlGetVersion()
	return fmt.Sprintf("%d.%d build %d", v.MajorVersion, v.MinorVersion, v.BuildNumber), values
}

type inventoryService struct{ run func(context.Context) error }

func (s *inventoryService) Execute(_ []string, requests <-chan svc.ChangeRequest, status chan<- svc.Status) (bool, uint32) {
	status <- svc.Status{State: svc.StartPending}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- s.run(ctx) }()
	current := svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown}
	status <- current
	for {
		select {
		case err := <-done:
			if err != nil {
				return false, 1
			}
			return false, 0
		case request := <-requests:
			switch request.Cmd {
			case svc.Interrogate:
				status <- current
			case svc.Stop, svc.Shutdown:
				status <- svc.Status{State: svc.StopPending}
				cancel()
				<-done
				return false, 0
			}
		}
	}
}
func runPlatformService(run func(context.Context) error) error {
	isService, err := svc.IsWindowsService()
	if err != nil {
		return err
	}
	if !isService {
		return errors.New("--service requires Windows Service Control Manager")
	}
	return svc.Run("HybridRMMInventory", &inventoryService{run})
}
