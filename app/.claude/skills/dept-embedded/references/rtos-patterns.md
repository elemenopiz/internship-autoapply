# RTOS & Embedded Design Patterns

## FreeRTOS Task (Static Allocation)
```c
static StaticTask_t tcb;
static StackType_t stack[STACK_SIZE];
TaskHandle_t h = xTaskCreateStatic(entry, "Name", STACK_SIZE, NULL, PRIORITY, stack, &tcb);
```

## Producer-Consumer (ISR → Task)
```c
void sensor_isr(void) {
    reading_t r = read_register();
    BaseType_t w = pdFALSE;
    xQueueSendFromISR(queue, &r, &w);
    portYIELD_FROM_ISR(w);
}
void consumer(void *p) {
    reading_t r;
    for (;;) {
        if (xQueueReceive(queue, &r, pdMS_TO_TICKS(100)) == pdTRUE) process(&r);
        else handle_timeout();
    }
}
```

## Mutex for Shared Resource
```c
bool spi_transfer(const uint8_t *tx, uint8_t *rx, size_t len) {
    if (xSemaphoreTake(spi_mutex, pdMS_TO_TICKS(50)) != pdTRUE) return false;
    bool ok = hal_spi_transfer(tx, rx, len);
    xSemaphoreGive(spi_mutex);  // ALWAYS release
    return ok;
}
```

## Ring Buffer (Lock-Free, ISR-Safe)
```c
typedef struct { uint8_t *buf; size_t size; volatile size_t head, tail; } ring_t;
bool ring_put(ring_t *r, uint8_t b) {
    size_t next = (r->head + 1U) & (r->size - 1U);
    if (next == r->tail) return false;
    r->buf[r->head] = b; r->head = next; return true;
}
bool ring_get(ring_t *r, uint8_t *b) {
    if (r->head == r->tail) return false;
    *b = r->buf[r->tail]; r->tail = (r->tail + 1U) & (r->size - 1U); return true;
}
```

## Watchdog Supervisor
```c
void watchdog_task(void *p) {
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(WDG_INTERVAL));
        taskENTER_CRITICAL(); uint32_t f = alive_flags; alive_flags = 0; taskEXIT_CRITICAL();
        if ((f & ALL_TASKS) == ALL_TASKS) hal_wdg_kick();
    }
}
```

## HAL Interface Pattern
```c
typedef struct hal_uart hal_uart_t;
typedef struct { uint32_t baud; uint8_t data_bits, stop_bits, parity; } hal_uart_cfg_t;
hal_status_t hal_uart_init(hal_uart_t *u, const hal_uart_cfg_t *cfg);
hal_status_t hal_uart_tx(hal_uart_t *u, const uint8_t *data, size_t len, uint32_t ms);
```

## Power Management
```c
void power_request_active(pm_t *pm, uint32_t id) {
    ENTER_CRITICAL(); pm->mask |= (1U << id); EXIT_CRITICAL();
}
void power_release_active(pm_t *pm, uint32_t id) {
    ENTER_CRITICAL(); pm->mask &= ~(1U << id); EXIT_CRITICAL();
}
```

## Build Info in Firmware
```c
__attribute__((section(".build_info")))
const build_info_t info = {
    .magic=0xDEADBEEF, .major=FW_MAJOR, .minor=FW_MINOR, .patch=FW_PATCH,
    .git_hash=GIT_HASH, .build_date=BUILD_DATE
};
```
