---
name: dept-embedded
description: >
  Specialist Department: Embedded & Firmware. Activated when project targets MCU,
  RTOS, bare-metal, or hardware-coupled code. Injects additional requirements into
  ALL roles. Trigger for "firmware", "embedded", "MCU", "RTOS", "bare-metal",
  "STM32", "ESP32", "nRF", "FreeRTOS", "Zephyr", "interrupt", "DMA", "HAL",
  "BSP", "register", "MISRA", "watchdog", "linker script", "JTAG", "SWD",
  "safety-critical", "IEC 61508", "ISO 26262", "DO-178C", or any HW topic.
---

# Specialist Department: Embedded & Firmware

## Activation
This department activates when ANY of these are detected: cross-compilation toolchain,
linker scripts (.ld), RTOS headers, register access (volatile uint32_t *), HAL/BSP
directories, MCU-specific configs (.ioc, sdkconfig, .dts), MISRA flags, or user
explicitly states embedded target.

---

## Phase Injections Into All Roles

### → Requirements Analyst
Capture additional requirements:
- **Target MCU/MPU**: Part number, architecture, clock, Flash, RAM.
- **Peripherals**: UART, SPI, I2C, ADC, CAN, USB, DMA channels.
- **Timing budgets**: Interrupt latency, control loop period, boot time (hard/soft deadlines).
- **Memory budgets**: Flash code, RAM static, RAM stack, RAM heap per feature.
- **Power**: Source, modes (active/sleep/deep sleep/shutdown), battery life target, wake sources.
- **Safety**: IEC 61508 / ISO 26262 / DO-178C / IEC 62304 level. MISRA compliance level.
- **Environmental**: Temperature range, vibration, humidity, EMC/EMI.

### → System Engineer
Additional design requirements:
- **Layered architecture**: Application → Service/Middleware → HAL (driver) → BSP → Hardware.
  Application NEVER touches registers directly. Portability: swap BSP+HAL for different MCU.
- **Memory map**: Flash regions, RAM regions, peripheral addresses, stack allocations per task.
- **Interrupt strategy**: Priority table, latency budgets, ISR handler rules, nested config.
- **State machines**: System modes (INIT→IDLE→ACTIVE→SLEEP→ERROR) with transitions.
- **DMA assignments**: Channel ownership, buffer allocation, completion signaling.
- **Power management**: Who controls mode transitions, peripheral active/release protocol.

### → Backend Developer (Firmware Developer)
Additional coding rules:
- **No dynamic allocation** (`malloc`/`new`) in real-time or safety-critical paths. Use static
  allocation, memory pools, or pre-allocated buffers.
- **volatile** on ALL hardware registers and ALL ISR-shared variables.
- **Critical sections** (ENTER_CRITICAL/EXIT_CRITICAL) around multi-word shared data.
- **ISRs must be short**: Set flag/semaphore, copy minimal data, return. No blocking calls.
  No malloc. No printf. No mutex lock.
- **No floating-point in ISRs** unless FPU context save is verified.
- **Register access**: Read-modify-write with named bit masks. Never raw hex magic numbers.
  ```c
  #define UART_CR1_TXEIE  (1U << 7)
  USART1->CR1 |= UART_CR1_TXEIE;  // Named mask, not magic 0x80
  ```
- **Fixed-width types**: uint8_t, uint16_t, uint32_t everywhere. Never `int` for HW interface.
- **MISRA-C:2012 compliance** where applicable. See references/misra-guidelines.md.
  Key rules: No recursion (17.2). Use return values (17.7). No malloc (21.3). No stdio (21.6).
  Parenthesize macros (20.7). Every if-else chain has final else (15.7). Every switch has default (16.4).
- **C++ embedded subset** (when using C++): No exceptions (`-fno-exceptions`). No RTTI
  (`-fno-rtti`). No `new`/`delete` in RT paths. Use RAII, constexpr, templates, enum class.
  Allowed: classes, references, namespaces, std::array, std::span. Restricted: virtual
  functions (measure vtable cost), std::function (heap), STL containers (heap).
- **Preprocessor discipline**: Header guards on ALL headers. Parenthesize all macro params.
  Prefer inline functions over function-like macros. Document all #ifdef branches.
- **Build flags**: `-Wall -Wextra -Werror -Wconversion -Wsign-conversion -Wdouble-promotion
  -ffunction-sections -fdata-sections` (dead code elimination). `-Wl,--gc-sections -Wl,--print-memory-usage`.

### → Unit Tester
Additional test strategy:
- **Level 1 (Host)**: Compile application logic for host (x86). Mock ALL HAL interfaces.
  Test pure logic. Use CppUTest/Unity/GoogleTest. Runs in CI in milliseconds.
- **Level 2 (Host Integration)**: Fake HAL implementations. Test module interactions,
  state machines, protocol parsers.
- **Level 3 (Target Integration)**: Flash to real MCU via JTAG/SWD. Test peripheral init,
  timing, interrupts.
- **Level 4 (HIL)**: Real MCU + simulated sensors/actuators. Automated test equipment.
  Required for safety-critical.
- **Level 5 (System Validation)**: Complete system in target environment.

Additional test cases:
- Peripheral init → all registers configured correctly.
- Timeout handling → recovery when hardware unresponsive.
- ISR latency → executes within timing budget.
- Stack usage → high-water mark within allocated budget.
- DMA → completes and signals correctly.
- Power modes → enter/exit without peripheral state loss or data corruption.

### → Integration Tester
- Test cross-module interactions with real peripheral drivers (Level 3+).
- Test system state machine transitions under realistic stimuli.
- Test watchdog recovery and fault handling.
- Test communication protocols end-to-end (UART/SPI/I2C/CAN/BLE).

### → Security Engineer
Additional firmware security checks:
- [ ] Secure boot chain (verify firmware signature before execution).
- [ ] Firmware updates signed AND encrypted. Rollback protection (version counter).
- [ ] Debug interfaces (JTAG/SWD) disabled or protected in production builds.
- [ ] Per-device unique keys provisioned (not shared across all units).
- [ ] Secure key storage (hardware crypto element if available, not in plain Flash).
- [ ] Stack canaries enabled (`-fstack-protector-strong`).
- [ ] MPU (Memory Protection Unit) configured to isolate tasks/privileged code.
- [ ] Constant-time comparison for secrets (no early termination on mismatch).
- [ ] No timing-dependent branches on secret data (side-channel resistance).

### → Release Engineer
Additional release artifacts:
- `.elf` — Debug binary with symbols (for JTAG debugging).
- `.bin` or `.hex` — Flashable binary image.
- `.map` — Linker map file for memory usage analysis.
- **Size report** — Flash and RAM usage breakdown per module/section.
- **Build info** — Git hash, timestamp, compiler version embedded in firmware binary.
  ```c
  __attribute__((section(".build_info")))
  const build_info_t info = {
      .magic = 0xDEADBEEFU,
      .version = { FW_MAJOR, FW_MINOR, FW_PATCH },
      .git_hash = GIT_HASH,
      .build_date = BUILD_DATE,
      .compiler = __VERSION__
  };
  ```
- **Programming instructions** — How to flash (J-Link, ST-Link, esptool, west flash).

---

## Common Embedded Frameworks

| Framework/RTOS  | Target                | Key Concepts                              |
|-----------------|-----------------------|-------------------------------------------|
| FreeRTOS        | Cortex-M, ESP32       | Tasks, queues, semaphores, timers         |
| Zephyr          | Multi-arch            | Device tree, Kconfig, west build system   |
| ESP-IDF         | ESP32 family          | FreeRTOS-based, menuconfig, partitions    |
| STM32 HAL/LL    | STM32                 | HAL (portable) / LL (performance/direct)  |
| nRF SDK/Connect | Nordic nRF52/53       | Zephyr-based, BLE/Thread/Zigbee stacks   |
| Arduino         | AVR, ESP, SAMD, RP2040| setup()/loop(), library ecosystem         |
| Mbed OS         | Cortex-M              | RTOS + drivers + connectivity             |
| CMSIS           | ARM Cortex            | Core access, DSP library, RTOS API        |
| ThreadX (Azure) | Multi-arch            | Tasks, queues, certified for safety       |
| QNX             | Safety-critical auto  | Microkernel, POSIX, ISO 26262             |
| VxWorks         | Aerospace/defense     | DO-178C certified, ARINC 653              |
| RTEMS           | Space/science         | POSIX, multi-arch, open source            |

---

## Reference Files

- `references/misra-guidelines.md` — MISRA-C:2012 critical rules, deviation process, static analysis tools.
- `references/rtos-patterns.md` — FreeRTOS task/queue/mutex, ring buffers, watchdog, DMA, HAL interface, power management, build info embedding.
