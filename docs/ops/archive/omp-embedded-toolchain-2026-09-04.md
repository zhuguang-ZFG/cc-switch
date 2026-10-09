# Embedded Toolchain PATH + OMP LSP Setup — 2026-09-04

## Goal

Make the ESP32 embedded toolchain discoverable by agents (`挂 PATH`) and verified for OMP LSP diagnostics. Continue from `omp-lsp-duckdb-tooling-2026-09-04.md`.

## PATH changes (user registry, persistent)

Appended 11 directories to `HKCU\Environment\Path` via `winreg` (type-preserving), then broadcast `WM_SETTINGCHANGE` so Explorer-spawned processes pick it up.

| Dir | Tool(s) | Pairing evidence |
|---|---|---|
| `C:\Espressif\tools\xtensa-esp-elf\esp-15.2.0_20251204\xtensa-esp-elf\bin` | xtensa gcc/ld/objdump | IDF v6.0.1 install log (`eim-install-v6.0.1.stdout.log` pins 15.2.0_20251204; the 15.2.0 copy lives on C:) |
| `D:\zhugu-home\.espressif\tools\riscv32-esp-elf\esp-15.2.0_20251204\riscv32-esp-elf\bin` | riscv gcc (esp32c5/c3) | IDF v6.0.1 pairing |
| `D:\zhugu-home\.espressif\tools\xtensa-esp-elf-gdb\16.3_20250913\xtensa-esp-elf-gdb\bin` | xtensa-esp32{s2,s3,}-elf-gdb | both IDFs |
| `D:\zhugu-home\.espressif\tools\riscv32-esp-elf-gdb\16.3_20250913\riscv32-esp-elf-gdb\bin` | riscv gdb | both IDFs |
| `D:\zhugu-home\.espressif\tools\openocd-esp32\v0.12.0-esp32-20260304\openocd-esp32\bin` | openocd | newest of two; IDF v6.0.1 log |
| `C:\Espressif\tools\esp-clang\esp-20.1.1_20250829\esp-clang\bin` | clangd (esp fork) | only copy on machine |
| `D:\zhugu-home\.espressif\tools\idf-exe\1.0.3` | `idf.py` | note: binary is `idf.py.exe`; command name is `idf.py` |
| `D:\zhugu-home\.espressif\tools\ninja\1.12.1` | ninja | |
| `D:\zhugu-home\.espressif\tools\cmake\3.30.2\bin` | cmake (resolves 3.30.2, listed before 4.0.3 deliberately) | IDF prefers <4.x |
| `D:\zhugu-home\.espressif\tools\cmake\4.0.3\bin` | cmake 4.0.3 (shadowed) | |
| `D:\zhugu-home\.espressif\tools\dfu-util\0.11\dfu-util-0.11-win64` | dfu-util | |

Notes:

- IDF v5.5.2 pins xtensa 14.2.0 (D-side dirs `esp-14.2.0_20251107` / `_20260121`); only 14.2.0_20260121 was NOT added to PATH to avoid shadowing (same binary names). v5.5.2 builds go through their own `export` script which prepends the right dir.
- `idf.py` alone is not a full environment: builds still need `IDF_PATH` + the toolchain env (`export.ps1`/`export.bat`). PATH only makes the *bare binaries* discoverable (`idf.py --version` → v1.0.3 works; full builds use the wrapper).
- Takes effect in **new** terminals/processes only. Already-running processes (including live OMP sessions) keep the old env.

## Two tool-quirks discovered (important for agents)

1. **OMP bash exec layer resolves via PATH only.** Direct invocation by absolute path (`/c/Espressif/.../clangd.exe --version`) fails with `command not found` even though `ls` sees the file. Bare names through `$PATH` work. Tool discovery must go through PATH, never absolute paths.
2. **The bash wrapper passes POSIX-style PATH to native children unconverted.** `PATH="/c/...:$PATH" omp.exe ...` gives the OMP process a PATH with `/c/...` entries that Windows cannot use — clangd etc. stay unresolvable. Real terminals launched from Explorer inherit the registry PATH (Windows format) and are unaffected. When testing "new session" behavior from an OMP bash, inject the registry PATH in Windows format instead.

## OMP LSP: clangd

`~/.omp/agent/lsp.json` gained a `clangd` override (commit `ced7eb2`):

```json
"clangd": {
  "rootMarkers": ["CMakeLists.txt", "sdkconfig", "compile_commands.json", "Makefile", "*.c", "*.cpp", "*.h"]
}
```

- `clangd` is a built-in OMP server; the override only replaces `rootMarkers` (command/fileTypes inherited). The built-in command name `clangd` now resolves through the updated PATH.
- **End-to-end verified**: throwaway project (`CMakeLists.txt` + `main.c` with deliberate `int x = "type-mismatch";`), fresh `omp -p` session (registry PATH injected) → clangd reported `main.c:2:22 error: Incompatible pointer to integer conversion initializing 'int' with an expression of type 'char[14]' (-Wint-conversion)`.
- ESP-IDF projects get full IntelliSense only with `build/compile_commands.json` present (produced by idf.py builds automatically).

## DAP debugging: BLOCKED by toolchain build (documented defect)

Attempted a `dap.json` entry (`esp-gdb` adapter → `xtensa-esp32s3-elf-gdb.exe -i dap`, pattern from OMP `omp://tools/debug.md` GDB+OpenOCD example). Result:

- Adapter id **was resolved** from `dap.json` (config format confirmed correct).
- Transport died immediately; manual pipe probe: **`GDB was compiled without threading, which DAP requires`** — the esp-gdb 16.3_20250913 Windows build ships without thread support, so the DAP interpreter cannot run. This is an upstream toolchain build limitation, not an OMP/config issue.

Action taken: `dap.json` **removed** (do not ship a known-broken adapter; it would poison launch auto-selection for `.elf` targets).

Alternatives when on-target debugging is needed:

- openocd `gdbserver :3333` + manual `xtensa-esp32s3-elf-gdb -ex "target remote :3333"` sessions (works today, no DAP).
- Revisit if Espressif ships a threading-enabled gdb or an `llvm-esp` lldb usable by the built-in `codelldb` adapter.

## Toolchain pairing reference

| IDF | xtensa gcc | riscv gcc | gdb | location evidence |
|---|---|---|---|---|
| v6.0.1 (D:\zhugu-home\.espressif\v6.0.1) | 15.2.0_20251204 | 15.2.0_20251204 | 16.3_20250913 | `eim-install-v6.0.1.stdout.log` |
| v5.5.2 (D:\zhugu-home\.espressif\esp-idf-v5.5.2) | 14.2.0_20251107/_20260121 | 14.2.0_20251107/_20260121 | 16.3_20250913 | D-side dirs |

clangd: esp-20.1.1_20250829 (Espressif llvm-project fork, `C:\Espressif\tools\esp-clang`).
