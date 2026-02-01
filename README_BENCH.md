# Benchmark

Comparison with mpremote v1.27.0 (January 2026).

Test files: 50 small (4 KB) + 4 large (50 KB) Python source files (total 400 KB).
Higher speed achieved by automatic compression of text-based files during transfer.

### RP2040 - USB-CDC - MacOS

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 9.6s (20.8 KB/s) | 23.3s (8.6 KB/s) | **2.4x** |
| small 50 x 4K - skip | 2.0s | 5.4s | **2.7x** |
| large 4 x 50K | 3.4s (58.8 KB/s) | 15s (13.3 KB/s) | **4.4x** |
| large 4 x 50K - skip | 0.6s | 0.9s | **1.5x** |

### ESP32-C6 - USB-CDC - MacOS

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 10.4s (19.2 KB/s) | 30.7s (6.5 KB/s) | **3.0x** |
| small 50 x 4K - skip | 5.5s | 12.7s | **2.3x** |
| large 4 x 50K | 4.0s (50.0 KB/s) | 13.2s (15.2 KB/s) | **3.3x** |
| large 4 x 50K - skip | 0.7s | 1.4s | **2.0x** |

### ESP32-WROOM - USB-UART - MacOS

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 32.4s (6.2 KB/s) | crash | - |
| small 50 x 4K - skip | 11.8s | crash | - |
| large 4 x 50K | 12.1s (16.5 KB/s) | crash | - |
| large 4 x 50K - skip | 3.5s | crash | - |

### ESP32-WROVER - USB-UART - MacOS

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 24.5s (8.2 KB/s) | crash | - |
| small 50 x 4K - skip | 7.7s | crash | - |
| large 4 x 50K | 12.0s (16.7 KB/s) | crash | - |
| large 4 x 50K - skip | 2.2s | crash | - |

### ESP32-WROOM - USB-UART - Linux

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 25.6s (7.8 KB/s) | 66.9s (3.0 KB/s) | **2.6x** |
| small 50 x 4K - skip | 5.9s | 17.3s | **2.9x** |
| large 4 x 50K | 7.9s (25.3 KB/s) | 44.4s (4.5 KB/s) | **5.6x** |
| large 4 x 50K - skip | 1.2s | 2.0s | **1.7x** |

### ESP32-WROVER - USB-UART - Linux

| files | mpytool | mpremote | speedup |
|-------|---------|----------|---------|
| small 50 x 4K | 33.9s (5.9 KB/s) | 75.0s (2.7 KB/s) | **2.2x** |
| small 50 x 4K - skip | 9.8s | 19.4s | **2.0x** |
| large 4 x 50K | 10.0s (20.0 KB/s) | 46.4s (4.3 KB/s) | **4.6x** |
| large 4 x 50K - skip | 1.6s | 2.5s | **1.6x** |

### Summary

- **Large files upload: 3.3x - 5.6x faster** than mpremote
- **Small files upload: 2.2x - 3.0x faster** than mpremote
- **Skip unchanged: 1.5x - 2.9x faster** than mpremote

Many more test scenarios could be designed (different file types, sizes, mixed workloads), but in most cases mpytool would be **at least 2x faster** than mpremote.
